from __future__ import annotations

import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from integrations.deeptutor_shchem_v1.presentation_workbench import (
    compose_deck_json,
    validate_presentation_input,
)


WORKSPACE = Path(__file__).resolve().parents[4]
OPENAPI = WORKSPACE / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
FIXTURE = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/fixtures/presentation_workbench_v1/fixture_a"
)


def _contract() -> dict:
    return yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))


def _validator(document: dict, schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(
        {
            "$ref": f"#/components/schemas/{schema_name}",
            "components": document["components"],
        }
    )


def _assert_valid(document: dict, schema_name: str, value: object) -> None:
    errors = list(_validator(document, schema_name).iter_errors(value))
    assert errors == [], [
        {"path": list(error.absolute_path), "message": error.message}
        for error in errors[:20]
    ]


def _theme_request() -> dict:
    return {
        "schema_version": "shchem.presentation-theme-request.v1",
        "scope": "wave1",
        "data_snapshot_id": "a" * 64,
        "theme_id": "THEME-REAL-01",
        "lesson_title": "电化学证据链",
        "grade": "高三",
        "duration_minutes": 45,
        "textbook_selection": {
            "book_title": "上海高中化学教材",
            "volume": "选择性必修1",
            "chapter": "第4章 氧化还原反应",
            "section": "4.4 金属的电化学腐蚀与防护",
            "publisher": "上海科技教育出版社",
            "evidence_note": "教师从本地教材目录明确选择本节。",
        },
        "lesson_goals": {
            "learning_objectives": ["能从共同材料中提取直接证据。"],
            "key_points": ["电极反应"],
            "difficult_points": ["组织因果链"],
            "prerequisites": ["氧化还原反应"],
            "lesson_emphasis": "先读共同材料，再按作答单元推进。",
        },
        "diagnosis": {
            "label": "教师填写的匿名班级薄弱点",
            "summary": "班级容易跳过共同材料直接下结论。",
            "common_error": "只写结论，没有引用现象。",
            "cause_hypothesis": "证据与结论之间缺少中间推理。",
            "confidence": 0.65,
            "counterevidence": ["尚未绑定完整班级样本。"],
        },
        "classroom_plan": {
            "teacher_questions": ["哪条现象是直接证据？"],
            "anticipated_responses": ["先指出可见变化。"],
            "homework": ["用证据—关系—结论三步重写答案。"],
        },
    }


def _deck() -> dict:
    raw = json.loads((FIXTURE / "input.json").read_text(encoding="utf-8"))
    normalized = validate_presentation_input(raw, asset_root=FIXTURE)
    return compose_deck_json(normalized, asset_root=FIXTURE)


def _envelope(data: dict) -> dict:
    return {
        "contract_version": "shchem.gateway.v1",
        "request_id": "req-presentation-openapi",
        "data": data,
    }


def _project() -> dict:
    return {
        "schema_version": "shchem.presentation-project.v1",
        "project_id": "PPTPRJ-" + "b" * 32,
        "title_zh": "电化学证据链",
        "description_zh": "完整主题：电化学与金属防护",
        "status": "draft",
        "candidate_only": True,
        "candidate_status": "candidate_only",
        "teacher_review_status": "pending_teacher_review",
        "publication_allowed": False,
        "created_at": "2026-08-28T08:00:00Z",
        "updated_at": "2026-08-28T08:01:00Z",
    }


def _outline() -> dict:
    return {
        "schema_version": "shchem.presentation-outline-projection.v1",
        "project_id": "PPTPRJ-" + "b" * 32,
        "revision": "PPTOL-" + "c" * 32,
        "deck_json": _deck(),
        "candidate_only": True,
        "candidate_status": "candidate_only",
        "teacher_review_status": "pending_teacher_review",
        "publication_allowed": False,
        "created_at": "2026-08-28T08:00:00Z",
        "updated_at": "2026-08-28T08:01:00Z",
        "idempotent_replay": False,
    }


def test_contract_registers_exact_presentation_routes_methods_and_binary_types() -> None:
    document = _contract()
    assert document["info"]["version"] == "1.25.0"
    expected = {
        "/api/v1/presentations/projects": {"get", "post"},
        "/api/v1/presentations/projects/{project_id}": {"get"},
        "/api/v1/presentations/projects/{project_id}/outline": {"get", "patch"},
        "/api/v1/presentations/projects/{project_id}/versions": {"get", "post"},
        "/api/v1/presentations/projects/{project_id}/versions/{version_id}": {"get"},
        "/api/v1/presentations/projects/{project_id}/versions/{version_id}/renders": {"post"},
        "/api/v1/presentations/jobs/{job_id}": {"get"},
        "/api/v1/presentations/jobs/{job_id}/cancel": {"post"},
        "/api/v1/presentations/jobs/{job_id}/retry": {"post"},
        "/api/v1/presentations/jobs/{job_id}/artifacts/{artifact_id}": {"get"},
    }
    observed = {
        path: set(item)
        for path, item in document["paths"].items()
        if path.startswith("/api/v1/presentations")
    }
    assert observed == expected
    for path, methods in expected.items():
        for method in methods:
            operation = document["paths"][path][method]
            assert operation["security"] == [{"bearerAuth": []}]
            assert {"401", "403", "503"}.issubset(operation["responses"])
            assert "loopback" in operation["description"].casefold()

    download = document["paths"][
        "/api/v1/presentations/jobs/{job_id}/artifacts/{artifact_id}"
    ]["get"]["responses"]["200"]
    assert set(download["content"]) == {
        "application/json",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "image/png",
    }
    assert download["headers"]["Content-Disposition"]["schema"]["pattern"] == "^attachment;"


def test_strict_request_rejects_paths_hash_authority_and_extra_identifiers() -> None:
    document = _contract()
    request = _theme_request()
    _assert_valid(document, "PresentationThemeRequest", request)
    validator = _validator(document, "PresentationThemeRequest")
    for field, value in (
        ("output_path", "C:/forbidden"),
        ("assets", []),
        ("answer_authority", "official"),
        ("student_name", "张某"),
    ):
        changed = dict(request)
        changed[field] = value
        assert list(validator.iter_errors(changed)), field


def test_project_outline_version_and_job_envelopes_are_machine_valid() -> None:
    document = _contract()
    for schema_name in (
        "PresentationThemeRequest",
        "PresentationProjectData",
        "PresentationDeckJson",
        "PresentationOutlineData",
        "PresentationVersionData",
        "PresentationVersionListData",
        "PresentationJobData",
        "PresentationProjectListEnvelope",
        "PresentationProjectCreateEnvelope",
        "PresentationProjectDetailEnvelope",
        "PresentationOutlineEnvelope",
        "PresentationVersionEnvelope",
        "PresentationVersionListEnvelope",
        "PresentationJobEnvelope",
    ):
        Draft202012Validator.check_schema(document["components"]["schemas"][schema_name])

    project = _project()
    outline = _outline()
    version = {
        "schema_version": "shchem.presentation-version.v1",
        "project_id": project["project_id"],
        "version_id": "PPTVER-" + "d" * 64,
        "source_outline_revision": outline["revision"],
        "immutable": True,
        "candidate_only": True,
        "candidate_status": "candidate_only",
        "teacher_review_status": "pending_teacher_review",
        "publication_allowed": False,
        "created_at": "2026-08-28T08:02:00Z",
        "idempotent_replay": False,
    }
    job = {
        "schema_version": "shchem.presentation-render-job.v1",
        "job_id": "PPTJOB-" + "e" * 64,
        "project_id": project["project_id"],
        "version_id": version["version_id"],
        "version_sha256": "d" * 64,
        "status": "queued",
        "attempt": 1,
        "retryable": False,
        "progress": {"stage": "queued", "percent": 0, "message_zh": "已进入本机队列。"},
        "artifacts": [],
        "qa_status": None,
        "candidate_only": True,
        "candidate_status": "candidate_only",
        "teacher_review_status": "pending_teacher_review",
        "publication_allowed": False,
        "error": None,
        "created_at": "2026-08-28T08:02:00Z",
        "updated_at": "2026-08-28T08:02:00Z",
        "started_at": None,
        "completed_at": None,
        "retry_required": False,
        "idempotent_replay": False,
    }
    _assert_valid(document, "PresentationProjectData", project)
    _assert_valid(document, "PresentationOutlineData", outline)
    _assert_valid(document, "PresentationVersionData", version)
    _assert_valid(
        document,
        "PresentationVersionListEnvelope",
        _envelope(
            {
                "schema_version": "shchem.presentation-version-list.v1",
                "versions": [version],
                "count": 1,
            }
        ),
    )
    _assert_valid(document, "PresentationJobData", job)
    _assert_valid(
        document,
        "PresentationProjectCreateEnvelope",
        _envelope(
            {
                "schema_version": "shchem.presentation-project-create.v1",
                "project": project,
                "outline": outline,
            }
        ),
    )
    _assert_valid(document, "PresentationJobEnvelope", _envelope(job))
