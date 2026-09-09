from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from PIL import Image

from integrations.deeptutor_shchem_v1.model_provider_probe import (
    ProbeTransportResponse,
)
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    ModelProviderProbeContext,
    ModelProviderSettingsError,
)
from integrations.deeptutor_shchem_v1.student_recommendation_workbench import (
    StudentRecommendationWorkbench,
)
from integrations.deeptutor_shchem_v1.student_visual_analysis import (
    StudentVisualAnalysisManager,
)

WORKSPACE = Path(__file__).resolve().parents[4]
OPENAPI = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
)
HTTP_METHODS = frozenset(
    {"get", "put", "post", "delete", "patch", "options", "head", "trace"}
)
NEW_PATHS = {
    "/api/v1/students": {"get", "post"},
    "/api/v1/submissions": {"post"},
    "/api/v1/submissions/{submission_id}/files": {"post"},
    "/api/v1/submissions/{submission_id}/files/{file_id}/content": {"put"},
    "/api/v1/submissions/{submission_id}": {"get"},
    "/api/v1/submissions/{submission_id}/status": {"get"},
    "/api/v1/submissions/{submission_id}/matching": {"get", "patch"},
    "/api/v1/submissions/{submission_id}/privacy-decisions": {"post"},
    "/api/v1/submissions/{submission_id}/analyze": {"post"},
    "/api/v1/submissions/{submission_id}/cancel": {"post"},
    "/api/v1/submissions/{submission_id}/analysis": {"get"},
    "/api/v1/submissions/{submission_id}/review": {"get"},
    "/api/v1/submissions/{submission_id}/scoring-decisions": {"post"},
    "/api/v1/submissions/{submission_id}/diagnostic-decisions": {"post"},
    "/api/v1/submissions/{submission_id}/diagnostic-review": {"get"},
    "/api/v1/submissions/{submission_id}/recommendation-preview": {"get"},
}
NEW_SCHEMA_NAMES = {
    "StudentVisualStudentCreateRequest",
    "StudentVisualStudentProfile",
    "StudentVisualStudentList",
    "StudentVisualSubmissionCreateRequest",
    "StudentVisualSubmissionFileRegisterRequest",
    "StudentVisualSubmissionFileRegisterReceipt",
    "StudentVisualSubmissionFile",
    "StudentVisualSubmission",
    "StudentVisualMatchingUpdateRequest",
    "StudentVisualMatchingResponse",
    "StudentVisualPrivacyDecisionRequest",
    "StudentVisualAnalyzeRequest",
    "StudentVisualCancelRequest",
    "StudentVisualAnalysisRequestSummary",
    "StudentVisualAnalysisResponseSummary",
    "StudentVisualAnalysisRun",
    "StudentVisualAnalysisCandidate",
    "StudentVisualAnalysis",
    "StudentVisualAnalysisView",
    "StudentVisualReviewView",
    "StudentVisualScoringDecisionRequest",
    "StudentVisualScoringDecisionReceipt",
    "StudentVisualScoringDecisionRecord",
    "StudentVisualDiagnosticDecisionRequest",
    "StudentVisualDiagnosticDecisionRecord",
    "StudentVisualDiagnosticDecisionReceipt",
    "StudentVisualDiagnosticReviewView",
    "StudentVisualRecommendationThemeCard",
    "StudentVisualRecommendationPreview",
}

REVISION = "rev_" + "1" * 32
TARGET_SECTION = "V1-C1:1.1"
RECOMMENDATION_SNAPSHOT = "c" * 64


def contract() -> dict[str, Any]:
    return yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))


def schema_validator(
    document: dict[str, Any], schema_name: str
) -> Draft202012Validator:
    return Draft202012Validator(
        {
            "$ref": f"#/components/schemas/{schema_name}",
            "components": document["components"],
        }
    )


def assert_valid(document: dict[str, Any], schema_name: str, instance: object) -> None:
    errors = list(schema_validator(document, schema_name).iter_errors(instance))
    assert errors == [], [
        {"path": list(error.absolute_path), "message": error.message}
        for error in errors[:20]
    ]


def iter_refs(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str):
            yield ref
        for child in value.values():
            yield from iter_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_refs(child)


def resolve_local_ref(document: object, ref: str) -> object:
    assert ref.startswith("#/"), f"external OpenAPI reference is not closed: {ref}"
    current = document
    for encoded_part in ref[2:].split("/"):
        part = encoded_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            assert part in current, f"unresolved OpenAPI reference: {ref}"
            current = current[part]
        elif isinstance(current, list):
            assert part.isdigit(), f"invalid array reference: {ref}"
            index = int(part)
            assert index < len(current), f"unresolved OpenAPI reference: {ref}"
            current = current[index]
        else:
            raise TypeError(f"OpenAPI reference crosses a scalar: {ref}")
    return current


def iter_object_schemas(
    value: object, path: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], dict[str, Any]]]:
    if isinstance(value, dict):
        if value.get("type") == "object":
            yield path, value
        for key, child in value.items():
            yield from iter_object_schemas(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_object_schemas(child, (*path, str(index)))


def png_bytes(color: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (480, 320), color).save(output, "PNG")
    return output.getvalue()


def curriculum_catalog() -> dict[str, Any]:
    """Return the same closed 5-volume/19-chapter/60-section shape as production."""

    volumes = []
    section_index = 0
    chapter_global = 0
    for volume_index, chapter_count in enumerate((4, 4, 4, 4, 3), 1):
        chapters = []
        for chapter_in_volume in range(1, chapter_count + 1):
            chapter_global += 1
            section_count = 4 if chapter_global <= 3 else 3
            chapter_id = f"V{volume_index}-C{chapter_in_volume}"
            sections = []
            for number in range(1, section_count + 1):
                section_index += 1
                section_number = f"{chapter_in_volume}.{number}"
                sections.append(
                    {
                        "section_key": f"{chapter_id}:{section_number}",
                        "section_id": None,
                        "section_number": section_number,
                        "section_title": f"第{section_index}节",
                        "display_label_zh": f"{section_number} 第{section_index}节",
                    }
                )
            chapters.append(
                {
                    "chapter_id": chapter_id,
                    "chapter_title": f"第{chapter_global}章 测试章",
                    "display_label_zh": f"第{chapter_global}章 测试章",
                    "sections": sections,
                }
            )
        volumes.append(
            {
                "volume_id": f"V{volume_index}",
                "volume_title": f"第{volume_index}册",
                "display_label_zh": f"第{volume_index}册",
                "chapters": chapters,
            }
        )
    assert chapter_global == 19
    assert section_index == 60
    return {
        "data_snapshot_id": "CURRICULUM-OPENAPI-TEST-V1",
        "counts": {"volumes": 5, "chapters": 19, "sections": 60},
        "volumes": volumes,
    }


def strict_question_search_card() -> dict[str, Any]:
    atomic_id = "REC-ATOMIC-1"
    source_metadata = {
        "source_tier": "candidate",
        "year": "2026",
        "region": "上海",
        "paper_type": "主题大题候选",
        "attribution_status": "source_identity_visible_candidate",
    }
    return {
        "scope": "master",
        "group_kind": "theme",
        "display_title_zh": "2026 上海化学主题一",
        "paper": {
            "id": "REC-PAPER-1",
            "title": "2026 上海化学候选卷",
            "order": 1,
            "order_status": "known_explicit",
            "status": "complete_parent_chain_inventory",
            "observed_theme_count": 1,
            "missing_theme_note_zh": None,
            "source_metadata": source_metadata,
        },
        "theme": {
            "id": "REC-THEME-1",
            "title": "物质的量与实验",
            "sequence": 1,
            "sequence_status": "known_explicit",
            "page_span": {
                "start_page": 1,
                "end_page": 1,
                "page_numbers": [1],
                "status": "explicit_evidence_union",
            },
            "parent_chain_status": "complete",
        },
        "source_metadata": {
            "question_bank_layer": "master",
            **source_metadata,
        },
        "counts": {
            "atomic_total": 1,
            "atomic_matched": 1,
            "display_atomic_units": 1,
        },
        "shared_context": {
            "context_summary_zh": "共同实验材料",
            "context_status": "explicit_visual_evidence",
            "material_count": 1,
            "materials": [
                {
                    "material_id": "REC-SHARED-1",
                    "type": "shared_experiment_context",
                    "page": 1,
                    "candidate_description_zh": "主题共用实验条件",
                    "preview_allowed": False,
                    "used_by_atomic_count": 1,
                }
            ],
        },
        "dependencies": {
            "independent": 1,
            "shared_material_only": 0,
            "one_prior_part": 0,
            "multiple_prior_parts": 0,
            "per_alias_unit": 0,
            "explicit_prior_edge_count": 0,
            "blocked": 0,
        },
        "matched_atomic_ids": [atomic_id],
        "match_details": [
            {
                "atomic_part_id": atomic_id,
                "reason_codes": ["curriculum_explicit_mapping_match"],
            }
        ],
        "atomic_chain": [
            {
                "atomic_part_id": atomic_id,
                "printed_question_id": "REC-PRINTED-1",
                "printed_question_number": "1",
                "printed_sequence": 1,
                "printed_sequence_status": "known_explicit",
                "atomic_sequence_in_printed": 1,
                "atomic_sequence_status": "known_explicit",
                "item_type": "short_fill",
                "label_summary": {
                    "status": "pending",
                    "source": "master_candidate",
                    "primary_K": None,
                    "supporting_K": [],
                    "A": [],
                    "C": [],
                    "R": [],
                    "RP": [],
                    "cognitive_prelabel": None,
                },
                "answer": {
                    "availability": "absent",
                    "source_authority": "none",
                    "has_quality_note": False,
                },
                "visual_coverage_kind": "unscanned",
                "dependency": {
                    "kind": "independent",
                    "prior_atomic_part_ids": [],
                    "explicit_prior_edge_count": 0,
                    "status": "explicit_master_value",
                },
                "visible_summary_zh": "依据共同材料完成计算。",
                "response_requirement_zh": "填写计算结果。",
                "theme_chain_role": {
                    "value": None,
                    "status": "blocked_pending_review",
                },
                "detail_endpoint": ("/api/v1/kb/workbench/master-atomic/REC-ATOMIC-1"),
                "alias_units": [],
            }
        ],
    }


def strict_recommendation_preview_sample() -> dict[str, Any]:
    submission_id = "SUB-" + "a" * 32
    result = StudentRecommendationWorkbench().preview(
        {
            "submission_id": submission_id,
            "data_snapshot_id": RECOMMENDATION_SNAPSHOT,
            "matches": [
                {
                    "match_id": "match-1",
                    "atomic_part_id": "CURRENT-ATOMIC-1",
                    "theme_id": None,
                    "paper_id": None,
                }
            ],
            "scoring_decisions": [
                {
                    "sequence": 1,
                    "match_id": "match-1",
                    "teacher_score": 0,
                    "maximum_score": 1,
                    "decision": "accept",
                    "curriculum_section_keys": [TARGET_SECTION],
                }
            ],
            "exclusions": {
                "atomic_part_ids": [],
                "theme_ids": [],
                "paper_ids": [],
            },
            "scopes": ["master"],
            "limit_per_section": 3,
        },
        curriculum_catalog_loader=curriculum_catalog,
        question_search_loader=lambda request: {
            "scope": request["scope"],
            "data_snapshot_id": RECOMMENDATION_SNAPSHOT,
            "items": [strict_question_search_card()],
            "authority": {"candidate_only": True, "read_only": True},
            "integrity": {
                "complete_theme_chain_returned": True,
                "dependency_context_preserved": True,
            },
        },
    )
    result.update(
        {
            "student_id": "00000000-0000-4000-8000-000000000001",
            "submission_revision": REVISION,
            "diagnostic_chain_head_sha256": "d" * 64,
            "diagnostic_review_status": "teacher_decisions_recorded",
            "mastery_written": False,
            "recommendation_written": False,
        }
    )
    return result


class FakeProviderStore:
    def __init__(self) -> None:
        self.revision = REVISION
        self.credential = True

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
            "allowed_data_classes": [
                "synthetic_only",
                "source_page_image",
                "student_answer_image",
            ],
            "image_egress": "teacher_confirmed_visual_pages",
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


def create_uploaded_submission(manager: StudentVisualAnalysisManager):
    student = manager.create_student(
        {"grade": "高三", "retention_days": 30, "consent_recorded": True}
    )
    submission = manager.create_submission(student["student_id"], {})
    for role, color in (("question_pages", "white"), ("student_work_pages", "ivory")):
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
    matching = manager.get_matching(student["student_id"], submission["submission_id"])
    matching = manager.update_matching(
        student["student_id"],
        submission["submission_id"],
        {
            "expected_revision": matching["revision"],
            "matches": matching["matching"]["matches"],
        },
    )
    submission["revision"] = matching["revision"]
    submission["matching"] = matching["matching"]
    return student, submission


def approve_privacy(manager, student, submission):
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
                "reference_answer_anchor": None,
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


@pytest.fixture(scope="module")
def live_student_visual_samples(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Any]:
    blocked_root = tmp_path_factory.mktemp("student-visual-openapi-blocked")
    blocked_manager = StudentVisualAnalysisManager(
        blocked_root / "private",
        project_root=None,
        provider_store=None,
    )
    complete_root = tmp_path_factory.mktemp("student-visual-openapi-complete")
    complete_transport = CapturingTransport()
    complete_manager = StudentVisualAnalysisManager(
        complete_root / "private",
        project_root=None,
        provider_store=FakeProviderStore(),
        transport=complete_transport,
        curriculum_catalog=curriculum_catalog(),
    )
    try:
        student, submission = create_uploaded_submission(blocked_manager)
        submission = approve_privacy(blocked_manager, student, submission)
        blocked = blocked_manager.analyze(
            student["student_id"],
            submission["submission_id"],
            {
                "expected_revision": submission["revision"],
                "provider_profile_id": "vision-profile",
                "provider_revision": REVISION,
            },
        )
        blocked_analysis = blocked_manager.get_analysis(
            student["student_id"], blocked["submission_id"]
        )

        cancelled_student = blocked_manager.create_student(
            {"grade": "高二", "retention_days": 30, "consent_recorded": True}
        )
        cancelled_submission = blocked_manager.create_submission(
            cancelled_student["student_id"], {}
        )
        cancelled = blocked_manager.cancel(
            cancelled_student["student_id"],
            cancelled_submission["submission_id"],
            {"expected_revision": cancelled_submission["revision"]},
        )

        complete_student, complete_submission = create_uploaded_submission(
            complete_manager
        )
        complete_submission = approve_privacy(
            complete_manager, complete_student, complete_submission
        )
        complete_transport.candidate = candidate_for(complete_submission)
        queued = complete_manager.analyze(
            complete_student["student_id"],
            complete_submission["submission_id"],
            {
                "expected_revision": complete_submission["revision"],
                "provider_profile_id": "vision-profile",
                "provider_revision": REVISION,
            },
        )
        completed = complete_manager.wait_for_terminal(
            complete_student["student_id"], queued["submission_id"]
        )
        analysis = complete_manager.get_analysis(
            complete_student["student_id"], completed["submission_id"]
        )
        decision = complete_manager.append_scoring_decision(
            complete_student["student_id"],
            completed["submission_id"],
            {
                "expected_revision": completed["revision"],
                "match_id": completed["matching"]["matches"][0]["match_id"],
                "teacher_score": 0,
                "reason": "Teacher checked the visible work.",
            },
        )
        diagnostic_decision = complete_manager.append_diagnostic_decision(
            complete_student["student_id"],
            completed["submission_id"],
            {
                "expected_revision": decision["revision"],
                "match_id": completed["matching"]["matches"][0]["match_id"],
                "scoring_decision_id": decision["decision"]["decision_id"],
                "decision": "accept",
                "result": "incorrect",
                "primary_error_type": "quantitative",
                "secondary_error_types": [],
                "curriculum_section_keys": [TARGET_SECTION],
                "teacher_note": "教师确认本题计算过程失分。",
            },
        )
        diagnostic_review = complete_manager.get_diagnostic_review(
            complete_student["student_id"], completed["submission_id"]
        )
        diagnostic_submission = complete_manager.get_submission(
            complete_student["student_id"], completed["submission_id"]
        )
        review = complete_manager.get_review(
            complete_student["student_id"], completed["submission_id"]
        )
        match_view = complete_manager.get_matching(
            complete_student["student_id"], completed["submission_id"]
        )
        return {
            "student": student,
            "submission": submission,
            "blocked": blocked,
            "blocked_analysis": blocked_analysis,
            "cancelled": cancelled,
            "complete_student": complete_student,
            "completed": completed,
            "analysis": analysis,
            "decision": decision,
            "diagnostic_decision": diagnostic_decision,
            "diagnostic_review": diagnostic_review,
            "diagnostic_submission": diagnostic_submission,
            "recommendation_preview": strict_recommendation_preview_sample(),
            "review": review,
            "matching": match_view,
            "complete_transport": complete_transport,
        }
    finally:
        blocked_manager.shutdown()
        complete_manager.shutdown()


def test_openapi_122_declares_student_visual_diagnosis_routes_and_preserves_existing_students_paths() -> (
    None
):
    document = contract()
    assert document["openapi"] == "3.1.0"
    assert document["info"]["version"] == "1.24.0"

    for path, methods in NEW_PATHS.items():
        path_item = document["paths"][path]
        assert set(path_item).intersection(HTTP_METHODS) == methods
        for method in methods:
            operation = path_item[method]
            assert operation["security"] == [{"bearerAuth": []}]
            if not (path == "/api/v1/students" and method == "get"):
                assert "401" in operation["responses"]
                assert "403" in operation["responses"]

    upload_content = document["paths"][
        "/api/v1/submissions/{submission_id}/files/{file_id}/content"
    ]["put"]["requestBody"]["content"]
    assert set(upload_content) == {
        "application/octet-stream",
        "image/png",
        "image/jpeg",
        "image/webp",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    assert upload_content["application/octet-stream"]["schema"] == {
        "type": "string",
        "format": "binary",
    }

    assert (
        document["paths"]["/api/v1/students"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        == "#/components/schemas/StudentVisualStudentCreateRequest"
    )
    assert (
        document["paths"]["/api/v1/submissions"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        == "#/components/schemas/StudentVisualSubmissionCreateRequest"
    )
    assert (
        document["paths"]["/api/v1/submissions/{submission_id}/analysis"]["get"][
            "responses"
        ]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/StudentVisualAnalysisView"
    )
    assert (
        document["paths"]["/api/v1/submissions/{submission_id}/review"]["get"][
            "responses"
        ]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/StudentVisualReviewView"
    )
    assert (
        document["paths"]["/api/v1/submissions/{submission_id}/diagnostic-decisions"][
            "post"
        ]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/StudentVisualDiagnosticDecisionRequest"
    )
    assert (
        document["paths"]["/api/v1/submissions/{submission_id}/diagnostic-decisions"][
            "post"
        ]["responses"]["201"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/StudentVisualDiagnosticDecisionReceipt"
    )
    assert (
        document["paths"]["/api/v1/submissions/{submission_id}/diagnostic-review"][
            "get"
        ]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/StudentVisualDiagnosticReviewView"
    )
    assert (
        document["paths"]["/api/v1/submissions/{submission_id}/recommendation-preview"][
            "get"
        ]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/StudentVisualRecommendationPreview"
    )


def test_student_visual_schema_objects_are_closed_and_all_refs_resolve() -> None:
    document = contract()
    schemas = document["components"]["schemas"]
    student_schemas = {
        name: schema
        for name, schema in schemas.items()
        if name.startswith("StudentVisual")
    }
    assert NEW_SCHEMA_NAMES.issubset(student_schemas)

    for name, schema in student_schemas.items():
        Draft202012Validator.check_schema(schema)
        for path, object_schema in iter_object_schemas(schema, (name,)):
            assert object_schema.get("additionalProperties") is False, "/".join(path)

    refs = set(iter_refs(document))
    assert refs
    for ref in sorted(refs):
        resolve_local_ref(document, ref)

    local_hold = schemas["StudentVisualSubmissionFileLocalHold"]
    assert local_hold["properties"]["contract_version"] == {
        "const": "shchem.student-image-local-hold.v1"
    }
    assert local_hold["properties"]["state"] == {"const": "awaiting_visual_provider"}
    assert local_hold["properties"]["ocr_invoked"] == {"const": False}
    assert local_hold["properties"]["transport_attempt_count"] == {"const": 0}

    analysis_view = schemas["StudentVisualAnalysisView"]
    assert analysis_view["properties"]["requires_teacher_review"] == {"const": True}
    assert analysis_view["properties"]["final_score"] == {"type": "null"}
    assert analysis_view["properties"]["long_term_update_allowed"] == {"const": False}

    cancel_request = schemas["StudentVisualCancelRequest"]
    assert cancel_request["required"] == ["expected_revision"]
    assert cancel_request["properties"] == {
        "expected_revision": {"$ref": "#/components/schemas/StudentVisualRevision"}
    }

    matching_maximum = schemas["StudentVisualSubmissionMatchingEntry"]["properties"][
        "maximum_score"
    ]
    assert matching_maximum == {
        "type": "number",
        "exclusiveMinimum": 0,
        "maximum": 100,
    }

    # While bytes are crossing the transport boundary the backend records an
    # explicit unknown value; it must never be misreported as model_invoked=false.
    assert schemas["StudentVisualAnalysisRun"]["properties"]["model_invoked"] == {
        "oneOf": [{"type": "boolean"}, {"type": "null"}]
    }

    scoring_record = schemas["StudentVisualScoringDecisionRecord"]
    assert scoring_record["properties"]["append_only"] == {"const": True}
    assert scoring_record["properties"]["long_term_update_allowed"] == {"const": False}
    assert scoring_record["properties"]["previous_record_sha256"]["oneOf"][1] == {
        "type": "null"
    }
    # The older scoring v1 request stays byte-for-byte compatible at the
    # schema boundary while diagnosis is added as a separate append-only step.
    scoring_request = schemas["StudentVisualScoringDecisionRequest"]
    assert scoring_request["required"] == [
        "expected_revision",
        "match_id",
        "teacher_score",
        "reason",
    ]
    assert set(scoring_request["properties"]) == set(scoring_request["required"])

    diagnostic_request = schemas["StudentVisualDiagnosticDecisionRequest"]
    assert set(diagnostic_request["required"]) == set(diagnostic_request["properties"])
    diagnostic_receipt = schemas["StudentVisualDiagnosticDecisionReceipt"]
    assert set(diagnostic_receipt["required"]) == set(diagnostic_receipt["properties"])
    diagnostic_review = schemas["StudentVisualDiagnosticReviewView"]
    assert set(diagnostic_review["required"]) == set(diagnostic_review["properties"])
    preview = schemas["StudentVisualRecommendationPreview"]
    assert set(preview["required"]) == set(preview["properties"])
    assert preview["properties"]["candidate_only"] == {"const": True}
    assert preview["properties"]["read_only"] == {"const": True}
    assert preview["properties"]["long_term_update_allowed"] == {"const": False}
    assert preview["properties"]["mastery_written"] == {"const": False}
    assert preview["properties"]["recommendation_written"] == {"const": False}

    status_values = schemas["StudentVisualSubmission"]["properties"]["status"]["enum"]
    assert "awaiting_matching_confirmation" in status_values
    event_type_values = schemas["StudentVisualEvent"]["properties"]["event_type"][
        "enum"
    ]
    assert "teacher_diagnostic_decision_appended" in event_type_values
    assert schemas["StudentVisualDiagnosticDecisionId"]["pattern"] == (
        "^SVDIAG-[0-9a-f]{32}$"
    )


def test_model_provider_contract_allows_explicit_student_page_vision_policy() -> None:
    document = contract()
    schemas = document["components"]["schemas"]
    expected_data_classes = {
        "synthetic_only",
        "question_text_redacted",
        "question_image_redacted",
        "source_page_image",
        "student_answer_image",
        "deidentified_student_text",
    }
    expected_egress = {
        "deny",
        "redacted_question_only",
        "teacher_confirmed_source_pages",
        "teacher_confirmed_student_pages",
        "teacher_confirmed_visual_pages",
    }

    shared_classes = schemas["ModelProviderAllowedDataClasses"]
    assert shared_classes["maxItems"] == len(expected_data_classes)
    assert set(shared_classes["items"]["enum"]) == expected_data_classes

    for schema_name in (
        "ModelProviderProfile",
        "ModelProviderOpenAIPresetUpsertRequest",
        "ModelProviderDeepSeekPresetUpsertRequest",
    ):
        properties = schemas[schema_name]["properties"]
        assert properties["allowed_data_classes"]["maxItems"] == len(
            expected_data_classes
        )
        assert set(properties["allowed_data_classes"]["items"]["enum"]) == (
            expected_data_classes
        )
        assert set(properties["image_egress"]["enum"]) == expected_egress

    for schema_name in (
        "ModelProviderCustomPublicUpsertRequest",
        "ModelProviderCustomLoopbackUpsertRequest",
    ):
        properties = schemas[schema_name]["properties"]
        assert properties["allowed_data_classes"] == {
            "$ref": "#/components/schemas/ModelProviderAllowedDataClasses"
        }
        assert set(properties["image_egress"]["enum"]) == expected_egress


def test_real_student_visual_responses_validate_the_openapi_contract(
    live_student_visual_samples: dict[str, Any],
) -> None:
    document = contract()
    assert_valid(
        document, "StudentVisualStudentProfile", live_student_visual_samples["student"]
    )
    assert_valid(
        document, "StudentVisualSubmission", live_student_visual_samples["blocked"]
    )
    assert_valid(
        document, "StudentVisualSubmission", live_student_visual_samples["cancelled"]
    )
    assert_valid(
        document,
        "StudentVisualSubmission",
        live_student_visual_samples["completed"],
    )
    request_summary = live_student_visual_samples["completed"]["analysis_runs"][0][
        "request"
    ]
    assert request_summary["data_classes"] == [
        "source_page_image",
        "student_answer_image",
    ]
    assert request_summary["page_sha256"] == [
        item["page_sha256"] for item in request_summary["role_classifications"]
    ]
    assert [
        (item["role"], item["data_class"])
        for item in request_summary["role_classifications"]
    ] == [
        ("question_pages", "source_page_image"),
        ("student_work_pages", "student_answer_image"),
    ]
    assert_valid(
        document,
        "StudentVisualAnalysisView",
        live_student_visual_samples["blocked_analysis"],
    )
    assert_valid(
        document,
        "StudentVisualAnalysisView",
        live_student_visual_samples["analysis"],
    )
    assert_valid(
        document,
        "StudentVisualMatchingResponse",
        live_student_visual_samples["matching"],
    )
    assert_valid(
        document,
        "StudentVisualScoringDecisionReceipt",
        live_student_visual_samples["decision"],
    )
    assert_valid(
        document,
        "StudentVisualReviewView",
        live_student_visual_samples["review"],
    )
    assert_valid(
        document,
        "StudentVisualDiagnosticDecisionReceipt",
        live_student_visual_samples["diagnostic_decision"],
    )
    assert_valid(
        document,
        "StudentVisualDiagnosticReviewView",
        live_student_visual_samples["diagnostic_review"],
    )
    assert_valid(
        document,
        "StudentVisualSubmission",
        live_student_visual_samples["diagnostic_submission"],
    )
    assert_valid(
        document,
        "StudentVisualRecommendationPreview",
        live_student_visual_samples["recommendation_preview"],
    )

    assert (
        live_student_visual_samples["blocked"]["analysis_runs"][-1]["status"]
        == "blocked"
    )
    assert (
        live_student_visual_samples["blocked"]["analysis_runs"][-1][
            "transport_attempt_count"
        ]
        == 0
    )
    for file_record in live_student_visual_samples["blocked"]["files"]:
        assert (
            file_record["local_hold"]["contract_version"]
            == "shchem.student-image-local-hold.v1"
        )
        assert file_record["local_hold"]["state"] == "awaiting_visual_provider"
        assert file_record["local_hold"]["ocr_invoked"] is False
        assert file_record["local_hold"]["transport_attempt_count"] == 0

    assert (
        live_student_visual_samples["analysis"]["analysis"]["requires_teacher_review"]
        is True
    )
    assert live_student_visual_samples["analysis"]["analysis"]["final_score"] is None
    assert (
        live_student_visual_samples["analysis"]["analysis"]["long_term_update_allowed"]
        is False
    )
    assert live_student_visual_samples["review"]["requires_teacher_review"] is True
    assert live_student_visual_samples["review"]["final_score"] is None
    assert live_student_visual_samples["review"]["long_term_update_allowed"] is False
    assert live_student_visual_samples["decision"]["requires_teacher_review"] is True
    assert live_student_visual_samples["decision"]["final_score"] is None
    assert live_student_visual_samples["decision"]["long_term_update_allowed"] is False
    assert live_student_visual_samples["decision"]["decision"]["append_only"] is True
    assert (
        live_student_visual_samples["decision"]["decision"]["long_term_update_allowed"]
        is False
    )
    diagnostic = live_student_visual_samples["diagnostic_decision"]
    assert diagnostic["decision"]["contract_version"] == (
        "shchem.student-diagnostic-decision.v1"
    )
    assert diagnostic["decision"]["decision_id"].startswith("SVDIAG-")
    assert (
        diagnostic["decision"]["scoring_decision_id"]
        == (live_student_visual_samples["decision"]["decision"]["decision_id"])
    )
    assert diagnostic["decision"]["curriculum_sections"][0]["section_key"] == (
        TARGET_SECTION
    )
    diagnostic_events = [
        event
        for event in live_student_visual_samples["diagnostic_submission"]["events"]
        if event["event_type"] == "teacher_diagnostic_decision_appended"
    ]
    assert len(diagnostic_events) == 1
    assert diagnostic_events[0]["details"]["decision_id"].startswith("SVDIAG-")
    assert diagnostic["mastery_written"] is False
    assert diagnostic["recommendation_written"] is False
    recommendation = live_student_visual_samples["recommendation_preview"]
    assert recommendation["diagnosis_status"] == "provisional_weakness"
    assert recommendation["metrics"]["independent_source_count"] == 1
    assert recommendation["metrics"]["stable_weakness_allowed"] is False
    assert recommendation["scope_groups"][0]["items"][0]["group_kind"] == "theme"
    assert (
        recommendation["scope_groups"][0]["items"][0]["basket_selection"]["kind"]
        == "theme"
    )
    assert recommendation["mastery_written"] is False
    assert recommendation["recommendation_written"] is False
    assert live_student_visual_samples["complete_transport"].calls == 1


def test_student_visual_contract_rejects_extras_and_false_safety_facts(
    live_student_visual_samples: dict[str, Any],
) -> None:
    document = contract()
    student_validator = schema_validator(document, "StudentVisualStudentProfile")
    submission_validator = schema_validator(document, "StudentVisualSubmission")
    analysis_validator = schema_validator(document, "StudentVisualAnalysisView")

    blocked = live_student_visual_samples["blocked"]
    analyzed = live_student_visual_samples["analysis"]

    serialized = json.dumps(
        {
            key: value
            for key, value in live_student_visual_samples.items()
            if key != "complete_transport"
        },
        ensure_ascii=False,
    )
    assert "sk-do-not-return" not in serialized

    invalid_student = {
        **live_student_visual_samples["student"],
        "unexpected": True,
    }
    invalid_submission = {
        **blocked,
        "analysis_runs": [*blocked["analysis_runs"], {"unexpected": True}],
    }
    invalid_analysis = {
        **analyzed,
        "analysis": {**analyzed["analysis"], "final_score": 1},
    }
    blocked_local_hold = {
        **blocked["files"][0]["local_hold"],
        "ocr_invoked": True,
    }

    assert not student_validator.is_valid(invalid_student)
    assert not submission_validator.is_valid(invalid_submission)
    assert not analysis_validator.is_valid(invalid_analysis)
    assert not schema_validator(
        document, "StudentVisualSubmissionFileLocalHold"
    ).is_valid(blocked_local_hold)


def test_diagnostic_and_recommendation_contracts_reject_widening_and_sensitive_fields(
    live_student_visual_samples: dict[str, Any],
) -> None:
    document = contract()
    schemas = document["components"]["schemas"]
    diagnostic_request_validator = schema_validator(
        document, "StudentVisualDiagnosticDecisionRequest"
    )
    valid_request = {
        "expected_revision": REVISION,
        "match_id": "match-1",
        "scoring_decision_id": "SVDEC-" + "1" * 32,
        "decision": "accept",
        "result": "incorrect",
        "primary_error_type": "quantitative",
        "secondary_error_types": ["expression"],
        "curriculum_section_keys": [TARGET_SECTION],
        "teacher_note": "教师已复核。",
    }
    assert diagnostic_request_validator.is_valid(valid_request)
    assert not diagnostic_request_validator.is_valid(
        {**valid_request, "unexpected": True}
    )
    assert not diagnostic_request_validator.is_valid(
        {**valid_request, "primary_error_type": "free_form_error"}
    )

    recommendation = live_student_visual_samples["recommendation_preview"]
    forbidden_keys = {
        "answer_text",
        "reference_answer_text",
        "solution_path_zh",
        "source_path",
        "local_path",
        "absolute_path",
        "crop_path",
        "url",
        "source_url",
        "ocr",
        "ocr_text",
    }

    def walk(value: object) -> None:
        if isinstance(value, dict):
            assert forbidden_keys.isdisjoint({str(key).casefold() for key in value})
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(recommendation)
    widened = json.loads(json.dumps(recommendation, ensure_ascii=False))
    widened["scope_groups"][0]["items"][0]["source_url"] = (
        "https://should-not-be-returned.example"
    )
    assert not schema_validator(
        document, "StudentVisualRecommendationPreview"
    ).is_valid(widened)

    recommendation_schema_text = json.dumps(
        {
            name: schema
            for name, schema in schemas.items()
            if name.startswith("StudentVisualRecommendation")
        },
        ensure_ascii=False,
        sort_keys=True,
    ).casefold()
    for forbidden in (
        '"answer_text"',
        '"source_path"',
        '"local_path"',
        '"source_url"',
        '"ocr"',
    ):
        assert forbidden not in recommendation_schema_text
