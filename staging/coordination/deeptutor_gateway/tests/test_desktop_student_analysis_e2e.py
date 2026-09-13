from __future__ import annotations

import contextlib
import hashlib
import json
import time
from dataclasses import asdict, fields, is_dataclass
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

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
from integrations.deeptutor_shchem_v1.intake_imports import RenderedPage
from integrations.deeptutor_shchem_v1.model_provider_probe import (
    ProbeTransportResponse,
)
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    ModelProviderProbeContext,
    ModelProviderSettingsError,
)
from integrations.deeptutor_shchem_v1.student_visual_analysis import (
    StudentVisualAnalysisManager,
)

PROFILE_ID = "vision-e2e-profile"
PROFILE_REVISION = "rev_" + "7" * 32
API_KEY_SENTINEL = "sk-e2e-secret-must-never-persist-or-project"
MODEL_ID = "vision-e2e-model"


class _UnusedReader:
    pass


class _CurriculumReader:
    def catalog(self) -> dict[str, Any]:
        return {
            "counts": {
                "volumes": 1,
                "chapters": 1,
                "sections": 1,
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
                                }
                            ],
                        }
                    ],
                }
            ],
        }


class _ProviderStore:
    """Facade metadata plus the manager's short-lived credential context."""

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
        self.allowed_data_classes = allowed_data_classes
        self.image_egress = image_egress

    def list_metadata(self) -> list[dict[str, Any]]:
        return [
            {
                "profile_id": PROFILE_ID,
                "display_name": "端到端假视觉模型",
                "base_url": "https://api.openai.com/v1",
                "model_id": MODEL_ID,
                "api_style": "responses",
                "capabilities": ["text", "vision", "structured_output"],
                "effective_capabilities": [
                    "text",
                    "vision",
                    "structured_output",
                ],
                "capability_evidence": {
                    "catalog": ["text", "vision", "structured_output"],
                    "declared": ["text", "vision", "structured_output"],
                    "probed": [],
                    "unknown": [],
                },
                "allowed_data_classes": list(self.allowed_data_classes),
                "image_egress": self.image_egress,
                "credential_state": "configured",
                "revision": PROFILE_REVISION,
            }
        ]

    def invocation_policy(
        self, profile_id: str, *, expected_revision: str
    ) -> dict[str, Any]:
        if profile_id != PROFILE_ID or expected_revision != PROFILE_REVISION:
            raise ModelProviderSettingsError(
                "revision_conflict", "provider settings changed", 409
            )
        return {
            "profile_id": PROFILE_ID,
            "revision": PROFILE_REVISION,
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
        return profile_id == PROFILE_ID

    @contextlib.contextmanager
    def borrow_invocation_context(self, profile_id: str, *, expected_revision: str):
        self.invocation_policy(profile_id, expected_revision=expected_revision)
        yield ModelProviderProbeContext(
            profile_id=PROFILE_ID,
            provider_id="openai",
            model_id=MODEL_ID,
            base_url_policy="openai_official_https_v1",
            base_url="https://api.openai.com/v1",
            revision=PROFILE_REVISION,
            api_key=API_KEY_SENTINEL,
            api_style="responses",
        )


class _DeterministicVisualTransport:
    """Answer from the request allowlist without contacting any provider."""

    def __init__(self) -> None:
        self.calls = 0
        self.saw_ephemeral_secret = False
        self.request_body_sha256: str | None = None

    @staticmethod
    def _candidate(manifest: dict[str, Any]) -> dict[str, Any]:
        page_hashes = [row["page_sha256"] for row in manifest["page_manifest"]]
        match = manifest["allowed_matches"][0]
        bbox = {"x": 0.1, "y": 0.1, "width": 0.6, "height": 0.3}
        maximum_score = float(match["maximum_score"])
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
                    "printed_question_id": match["printed_question_id"],
                    "question_number": match["question_number_hint"],
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
                    "visual_response_observation": "可见学生写出 NaCl，并给出数值结果。",
                    "chemistry_observations": {
                        "formulas": ["NaCl"],
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
                            "maximum_score": maximum_score,
                            "confidence": 0.9,
                            "blockers": [],
                        }
                    ],
                    "suggested_score": 1.0,
                    "maximum_score": maximum_score,
                    "confidence": 0.9,
                    "blockers": [],
                    "error_hypotheses": [
                        {
                            "hypothesis": "候选：概念表达可能不完整。",
                            "supporting_evidence": ["仅观察到简式"],
                            "counterevidence": ["仍需教师结合题意判断"],
                            "confidence": 0.55,
                        }
                    ],
                }
            ],
            "blockers": [],
            "requires_teacher_review": True,
            "final_score": None,
            "long_term_update_allowed": False,
        }

    def send(self, request, *, cancel_event, deadline_monotonic):
        assert not cancel_event.is_set()
        assert deadline_monotonic > time.monotonic()
        self.calls += 1
        self.saw_ephemeral_secret = request.api_key == API_KEY_SENTINEL
        self.request_body_sha256 = hashlib.sha256(request.body).hexdigest()

        body = json.loads(request.body)
        assert body["store"] is False
        assert body["background"] is False
        assert body["model"] == MODEL_ID
        content = body["input"][0]["content"]
        prompt = next(row["text"] for row in content if row["type"] == "input_text")
        images = [row for row in content if row["type"] == "input_image"]
        manifest = json.loads(prompt.split("\n", 1)[1])
        assert len(images) == len(manifest["page_manifest"]) == 2
        assert all(
            row["image_url"].startswith("data:image/png;base64,") for row in images
        )
        assert manifest["candidate_only"] is True
        assert manifest["requires_teacher_review"] is True
        assert manifest["final_score"] is None
        assert manifest["long_term_update_allowed"] is False

        candidate_text = json.dumps(
            self._candidate(manifest), ensure_ascii=False, sort_keys=True
        )
        response_body = json.dumps(
            {
                "status": "completed",
                "error": None,
                "incomplete_details": None,
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": candidate_text}],
                    }
                ],
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 34,
                    "total_tokens": 46,
                },
            },
            ensure_ascii=False,
        ).encode()
        return ProbeTransportResponse(
            http_status=200,
            content_type="application/json",
            content_encoding=None,
            body=response_body,
            latency_ms=1,
            model_invoked=True,
        )


def _write_png(path: Path, color: str) -> Path:
    Image.new("RGB", (640, 480), color).save(path, "PNG")
    return path


class _RepeatedPdfPageRenderer:
    """Controllable renderer that models a PDF containing the same page twice."""

    def render(
        self, source_path: Path, *, mime_type: str, work_root: Path
    ) -> list[RenderedPage]:
        work_root.mkdir(parents=True, exist_ok=False)
        if mime_type == "application/pdf":
            paths = [work_root / "page-0001.png", work_root / "page-0002.png"]
            for path in paths:
                Image.new("RGB", (640, 480), "white").save(path, "PNG")
            return [RenderedPage(path, "image/png") for path in paths]
        target = work_root / "page-0001.png"
        with Image.open(source_path) as opened:
            opened.convert("RGB").save(target, "PNG")
        return [RenderedPage(target, "image/png")]


def _all_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, nested in value.items():
            yield str(key)
            yield from _all_strings(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _all_strings(nested)


def _all_keys(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        result.update(str(key) for key in value)
        for nested in value.values():
            result.update(_all_keys(nested))
    elif isinstance(value, (list, tuple)):
        for nested in value:
            result.update(_all_keys(nested))
    return result


def _assert_closed_public_dto(value: Any, *, forbidden_values: tuple[str, ...]) -> None:
    assert is_dataclass(value)
    projection = asdict(value)
    keys = _all_keys(projection)
    assert {
        "private_relative_path",
        "private_state",
        "analysis_runs",
        "analysis",
        "events",
        "request",
        "response",
        "usage",
        "candidate",
        "model_id",
        "page_quality",
        "question_anchor",
        "student_answer_anchor",
        "reference_answer_anchor",
        "answer_region_anchor",
        "scoring_point_id",
        "supporting_evidence",
        "counterevidence",
        "final_score",
        "official_score",
        "long_term_materialization",
        "mastery_written",
        "recommendation_written",
    }.isdisjoint(keys)
    visible_strings = tuple(_all_strings(projection)) + (repr(value),)
    assert all(
        forbidden not in visible
        for forbidden in forbidden_values
        for visible in visible_strings
    )
    serialized = json.dumps(projection, ensure_ascii=False, sort_keys=True)
    assert "input_image" not in serialized
    assert "shchem_student_visual_analysis_candidate" not in serialized
    assert "data:image/png;base64," not in serialized


def _assert_nonrepr_bindings_are_hidden(value: Any) -> None:
    if not is_dataclass(value):
        return
    rendered = repr(value)
    for item in fields(value):
        nested = getattr(value, item.name)
        if not item.repr:
            for hidden in _all_strings(nested):
                if hidden:
                    assert hidden not in rendered
        if is_dataclass(nested):
            _assert_nonrepr_bindings_are_hidden(nested)
        elif isinstance(nested, (list, tuple)):
            for child in nested:
                if is_dataclass(child):
                    _assert_nonrepr_bindings_are_hidden(child)


def _build_facade(
    paths: DesktopPaths,
    manager: StudentVisualAnalysisManager,
    provider_store: _ProviderStore,
    curriculum: _CurriculumReader,
) -> DesktopWorkbenchFacade:
    return DesktopWorkbenchFacade(
        paths,
        theme_reader=_UnusedReader(),
        supplemental_reader=_UnusedReader(),
        curriculum_reader=curriculum,
        search_reader=_UnusedReader(),
        provider_store=provider_store,
        state_store=DesktopStateStore(paths.state_root),
        student_analysis_manager=manager,
    )


def _new_manager(
    paths: DesktopPaths,
    provider_store: _ProviderStore,
    transport: _DeterministicVisualTransport,
    curriculum: _CurriculumReader,
) -> StudentVisualAnalysisManager:
    return StudentVisualAnalysisManager(
        paths.state_root / "student-visual-v1",
        project_root=paths.workspace_root,
        provider_store=provider_store,
        transport=transport,
        curriculum_catalog=curriculum.catalog(),
        require_project_external=True,
    )


def _create_confirmed_submission(
    facade: DesktopWorkbenchFacade,
    *,
    question_files: list[Path],
    work_files: list[Path],
) -> tuple[StudentProfileSummary, StudentSubmissionSummary]:
    profile = facade.create_student_profile(
        grade="高三", retention_days=30, consent_recorded=True
    )
    submission = facade.create_student_submission(profile.student_id)
    submission = facade.add_student_submission_files(
        student_id=profile.student_id,
        submission_id=submission.submission_id,
        role="question_pages",
        files=question_files,
        expected_revision=submission.revision,
    )
    submission = facade.add_student_submission_files(
        student_id=profile.student_id,
        submission_id=submission.submission_id,
        role="student_work_pages",
        files=work_files,
        expected_revision=submission.revision,
    )
    edits = [
        {
            "match_id": match.match_id,
            "question_number_hint": str(index),
            "maximum_score": 1.0,
            "question_page_sha256": match.question_page_sha256,
            "student_work_page_sha256": match.student_work_page_sha256,
            "reference_answer_page_sha256": match.reference_answer_page_sha256,
        }
        for index, match in enumerate(submission.matches, 1)
    ]
    submission = facade.confirm_student_matching(
        student_id=profile.student_id,
        submission_id=submission.submission_id,
        expected_revision=submission.revision,
        edits=edits,
    )
    return profile, submission


def test_old_student_only_policy_is_blocked_and_public_dto_stays_closed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "sh-chem-db").mkdir(parents=True)
    (workspace / "integrations" / "deeptutor_shchem_v1").mkdir(parents=True)
    paths = DesktopPaths.from_workspace(
        workspace, state_root=tmp_path / "personal-state"
    )
    curriculum = _CurriculumReader()
    provider_store = _ProviderStore(
        allowed_data_classes=("synthetic_only", "student_answer_image"),
        image_egress="teacher_confirmed_student_pages",
    )
    transport = _DeterministicVisualTransport()
    manager = _new_manager(paths, provider_store, transport, curriculum)
    facade = _build_facade(paths, manager, provider_store, curriculum)
    question_path = _write_png(tmp_path / "private-question-name.png", "white")
    work_path = _write_png(tmp_path / "private-work-name.png", "ivory")
    try:
        profile, submission = _create_confirmed_submission(
            facade,
            question_files=[question_path],
            work_files=[work_path],
        )
        confirmation = facade.prepare_student_analysis_confirmation(
            student_id=profile.student_id,
            submission_id=submission.submission_id,
            profile_id=PROFILE_ID,
        )
        blocked = facade.start_student_analysis(
            student_id=profile.student_id,
            confirmation=confirmation,
            identifiers_clear=True,
            student_page_egress_confirmed=True,
        )

        assert blocked.status == "awaiting_visual_provider"
        assert transport.calls == 0
        raw = manager.get_submission(profile.student_id, submission.submission_id)
        assert raw["analysis_runs"][-1]["blocker"]["code"] == (
            "source_image_data_class_not_allowed"
        )
        _assert_closed_public_dto(
            blocked,
            forbidden_values=(
                API_KEY_SENTINEL,
                str(question_path.resolve()),
                str(work_path.resolve()),
                raw["analysis_runs"][-1]["blocker"]["message"],
            ),
        )
        _assert_nonrepr_bindings_are_hidden(blocked)
    finally:
        facade.shutdown()


def test_confirmation_rejects_same_pixels_across_question_and_work_roles(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "sh-chem-db").mkdir(parents=True)
    (workspace / "integrations" / "deeptutor_shchem_v1").mkdir(parents=True)
    paths = DesktopPaths.from_workspace(
        workspace, state_root=tmp_path / "personal-state"
    )
    curriculum = _CurriculumReader()
    provider_store = _ProviderStore()
    transport = _DeterministicVisualTransport()
    manager = _new_manager(paths, provider_store, transport, curriculum)
    facade = _build_facade(paths, manager, provider_store, curriculum)
    duplicate_path = _write_png(tmp_path / "duplicate-source.png", "white")
    try:
        profile, submission = _create_confirmed_submission(
            facade,
            question_files=[duplicate_path],
            work_files=[duplicate_path],
        )
        duplicate_hash = submission.pages[0].sha256

        with pytest.raises(DesktopFacadeError) as caught:
            facade.prepare_student_analysis_confirmation(
                student_id=profile.student_id,
                submission_id=submission.submission_id,
                profile_id=PROFILE_ID,
            )

        assert caught.value.code == "submission_page_duplicate"
        assert caught.value.message_zh == (
            "检测到内容完全相同的重复页面；请从所选文件中删除重复页后新建一次分析。"
        )
        assert str(duplicate_path.resolve()) not in str(caught.value)
        assert duplicate_hash not in str(caught.value)
        assert transport.calls == 0
    finally:
        facade.shutdown()


def test_confirmation_rejects_repeated_pixel_pages_from_one_pdf(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "sh-chem-db").mkdir(parents=True)
    (workspace / "integrations" / "deeptutor_shchem_v1").mkdir(parents=True)
    paths = DesktopPaths.from_workspace(
        workspace, state_root=tmp_path / "personal-state"
    )
    curriculum = _CurriculumReader()
    provider_store = _ProviderStore()
    transport = _DeterministicVisualTransport()
    manager = StudentVisualAnalysisManager(
        paths.state_root / "student-visual-v1",
        project_root=paths.workspace_root,
        provider_store=provider_store,
        renderer=_RepeatedPdfPageRenderer(),
        transport=transport,
        curriculum_catalog=curriculum.catalog(),
        require_project_external=True,
    )
    facade = _build_facade(paths, manager, provider_store, curriculum)
    pdf_path = tmp_path / "repeated-pages.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n% controlled duplicate-page fixture\n")
    work_path = _write_png(tmp_path / "student-work.png", "ivory")
    try:
        profile, submission = _create_confirmed_submission(
            facade,
            question_files=[pdf_path],
            work_files=[work_path],
        )
        question_pages = [
            page for page in submission.pages if page.role == "question_pages"
        ]
        assert len(question_pages) == 2
        assert question_pages[0].sha256 == question_pages[1].sha256

        with pytest.raises(DesktopFacadeError) as caught:
            facade.prepare_student_analysis_confirmation(
                student_id=profile.student_id,
                submission_id=submission.submission_id,
                profile_id=PROFILE_ID,
            )

        assert caught.value.code == "submission_page_duplicate"
        assert "删除重复页后新建一次分析" in caught.value.message_zh
        assert str(pdf_path.resolve()) not in str(caught.value)
        assert question_pages[0].sha256 not in str(caught.value)
        assert transport.calls == 0
    finally:
        facade.shutdown()


def test_desktop_student_analysis_round_trip_is_candidate_only_and_persistent(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "sh-chem-db").mkdir(parents=True)
    (workspace / "integrations" / "deeptutor_shchem_v1").mkdir(parents=True)
    paths = DesktopPaths.from_workspace(
        workspace, state_root=tmp_path / "personal-state"
    )
    curriculum = _CurriculumReader()
    provider_store = _ProviderStore()
    transport = _DeterministicVisualTransport()
    manager = _new_manager(paths, provider_store, transport, curriculum)
    facade = _build_facade(paths, manager, provider_store, curriculum)
    question_path = _write_png(tmp_path / "question.png", "white")
    work_path = _write_png(tmp_path / "student-work.png", "ivory")
    forbidden_values = (
        API_KEY_SENTINEL,
        str(question_path.resolve()),
        str(work_path.resolve()),
        str((paths.state_root / "student-visual-v1").resolve()),
    )
    public_dtos: list[Any] = []

    try:
        profile = facade.create_student_profile(
            grade="高三", retention_days=30, consent_recorded=True
        )
        assert isinstance(profile, StudentProfileSummary)
        submission = facade.create_student_submission(profile.student_id)
        assert isinstance(submission, StudentSubmissionSummary)
        public_dtos.extend((profile, submission))

        submission = facade.add_student_submission_files(
            student_id=profile.student_id,
            submission_id=submission.submission_id,
            role="question_pages",
            files=[question_path],
            expected_revision=submission.revision,
        )
        submission = facade.add_student_submission_files(
            student_id=profile.student_id,
            submission_id=submission.submission_id,
            role="student_work_pages",
            files=[work_path],
            expected_revision=submission.revision,
        )
        assert submission.page_counts_by_role == {
            "question_pages": 1,
            "reference_answer_pages": 0,
            "student_work_pages": 1,
        }
        assert [page.role for page in submission.pages] == [
            "question_pages",
            "student_work_pages",
        ]
        assert len(submission.matches) == 1

        provisional = submission.matches[0]
        submission = facade.confirm_student_matching(
            student_id=profile.student_id,
            submission_id=submission.submission_id,
            expected_revision=submission.revision,
            edits=[
                {
                    "match_id": provisional.match_id,
                    "question_number_hint": "1（教师已核对）",
                    "maximum_score": 2.0,
                    "question_page_sha256": provisional.question_page_sha256,
                    "student_work_page_sha256": (provisional.student_work_page_sha256),
                    "reference_answer_page_sha256": None,
                }
            ],
        )
        assert submission.can_analyze is True
        assert submission.matches[0].maximum_score == 2.0
        public_dtos.append(submission)

        confirmation = facade.prepare_student_analysis_confirmation(
            student_id=profile.student_id,
            submission_id=submission.submission_id,
            profile_id=PROFILE_ID,
        )
        assert isinstance(confirmation, StudentAnalysisConfirmation)
        assert confirmation.page_sha256 == tuple(
            page.sha256 for page in submission.pages
        )
        assert confirmation.total_page_count == 2
        assert confirmation.page_counts_by_role == submission.page_counts_by_role
        assert confirmation.provider_revision == PROFILE_REVISION
        public_dtos.append(confirmation)

        queued = facade.start_student_analysis(
            student_id=profile.student_id,
            confirmation=confirmation,
            identifiers_clear=True,
            student_page_egress_confirmed=True,
        )
        assert queued.status in {
            "queued_for_analysis",
            "analyzing",
            "awaiting_teacher_review",
        }
        public_dtos.append(queued)

        deadline = time.monotonic() + 5.0
        terminal = queued
        while time.monotonic() < deadline:
            terminal = facade.student_submission(
                student_id=profile.student_id,
                submission_id=submission.submission_id,
            )
            if terminal.status == "awaiting_teacher_review":
                break
            assert terminal.status in {"queued_for_analysis", "analyzing"}
            time.sleep(0.01)
        assert terminal.status == "awaiting_teacher_review"
        assert terminal.candidate_available is True
        assert transport.calls == 1
        assert transport.saw_ephemeral_secret is True
        assert transport.request_body_sha256 is not None
        public_dtos.append(terminal)

        review = facade.student_analysis_review(
            student_id=profile.student_id,
            submission_id=submission.submission_id,
        )
        assert isinstance(review, StudentAnalysisReview)
        assert review.candidate_only is True
        assert review.long_term_update_allowed is False
        assert review.review_complete is False
        assert review.scoring_confirmed_count == 0
        assert review.diagnostic_confirmed_count == 0
        assert len(review.items) == 1
        assert review.items[0].maximum_score == 2.0
        assert review.items[0].suggested_score == 1.0
        assert review.items[0].latest_teacher_score is None
        public_dtos.append(review)

        scored = facade.record_student_score(
            student_id=profile.student_id,
            submission_id=submission.submission_id,
            expected_revision=review.revision,
            match_id=review.items[0].match_id,
            teacher_score=1.5,
            reason="教师已逐页核对题面与学生作答。",
        )
        assert scored.items[0].latest_teacher_score == 1.5
        assert scored.items[0].latest_scoring_decision_id is not None
        assert scored.review_complete is False
        public_dtos.append(scored)

        section = facade.student_curriculum_sections()[0]
        diagnosed = facade.record_student_diagnosis(
            student_id=profile.student_id,
            submission_id=submission.submission_id,
            expected_revision=scored.revision,
            match_id=scored.items[0].match_id,
            scoring_decision_id=scored.items[0].latest_scoring_decision_id,
            decision="edit",
            result="partial",
            primary_error_type="concept",
            secondary_error_types=("chemical_language",),
            curriculum_section_keys=(section.section_key,),
            teacher_note="教师确认概念表达和化学用语均需订正。",
        )
        assert diagnosed.items[0].latest_teacher_score == 1.5
        assert diagnosed.items[0].latest_diagnostic_decision == "教师已修改诊断"
        assert diagnosed.review_complete is True
        assert diagnosed.candidate_only is True
        assert diagnosed.long_term_update_allowed is False
        public_dtos.extend((section, diagnosed))

        raw_submission = manager.get_submission(
            profile.student_id, submission.submission_id
        )
        raw_review = manager.get_review(profile.student_id, submission.submission_id)
        raw_diagnosis = manager.get_diagnostic_review(
            profile.student_id, submission.submission_id
        )
        assert raw_submission["final_score"] is None
        assert raw_submission["long_term_materialization"] == {
            "allowed": False,
            "attempt_written": False,
            "mastery_written": False,
            "recommendation_written": False,
            "reason": "single_submission_teacher_review_candidate_only",
        }
        assert raw_review["final_score"] is None
        assert raw_review["legacy_attempt_written"] is False
        assert raw_review["mastery_written"] is False
        assert raw_review["recommendation_written"] is False
        assert raw_diagnosis["final_score"] is None
        assert raw_diagnosis["legacy_attempt_written"] is False
        assert raw_diagnosis["mastery_written"] is False
        assert raw_diagnosis["recommendation_written"] is False
        assert "official_score" not in json.dumps(raw_submission, ensure_ascii=False)

        for dto in public_dtos:
            _assert_closed_public_dto(dto, forbidden_values=forbidden_values)
            _assert_nonrepr_bindings_are_hidden(dto)
        assert "final_score" not in {item.name for item in fields(diagnosed)}
        assert "official_score" not in {item.name for item in fields(diagnosed)}
        assert "official_score" not in {
            item.name for item in fields(diagnosed.items[0])
        }
    finally:
        facade.shutdown()

    persisted_files = [
        path
        for path in (paths.state_root / "student-visual-v1").rglob("*")
        if path.is_file()
    ]
    assert persisted_files
    assert all(
        API_KEY_SENTINEL.encode() not in path.read_bytes() for path in persisted_files
    )

    reopened_manager = _new_manager(paths, provider_store, transport, curriculum)
    reopened = _build_facade(paths, reopened_manager, provider_store, curriculum)
    try:
        profiles = reopened.student_profiles()
        submissions = reopened.student_submissions(
            student_id=profile.student_id, limit=10
        )
        restored_review = reopened.student_analysis_review(
            student_id=profile.student_id,
            submission_id=submission.submission_id,
        )

        assert len(profiles) == 1
        assert profiles[0].student_id == profile.student_id
        assert len(submissions) == 1
        assert submissions[0].submission_id == submission.submission_id
        assert submissions[0].status == "awaiting_teacher_review"
        assert submissions[0].scoring_confirmed_count == 1
        assert submissions[0].diagnostic_confirmed_count == 1
        assert restored_review.review_complete is True
        assert restored_review.items[0].latest_teacher_score == 1.5
        assert restored_review.items[0].latest_diagnostic_decision == "教师已修改诊断"
        assert restored_review.candidate_only is True
        assert restored_review.long_term_update_allowed is False
        assert transport.calls == 1

        for dto in (*profiles, *submissions, restored_review):
            _assert_closed_public_dto(dto, forbidden_values=forbidden_values)
            _assert_nonrepr_bindings_are_hidden(dto)

        restored_raw_review = reopened_manager.get_review(
            profile.student_id, submission.submission_id
        )
        restored_raw_diagnosis = reopened_manager.get_diagnostic_review(
            profile.student_id, submission.submission_id
        )
        for raw in (restored_raw_review, restored_raw_diagnosis):
            assert raw["final_score"] is None
            assert raw["long_term_update_allowed"] is False
            assert raw["mastery_written"] is False
            assert raw["recommendation_written"] is False
    finally:
        reopened.shutdown()
