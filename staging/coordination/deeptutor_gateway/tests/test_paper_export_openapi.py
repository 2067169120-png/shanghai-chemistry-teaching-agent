from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

WORKSPACE = Path(__file__).resolve().parents[4]
OPENAPI = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
)
START_PATH = "/api/v1/prep/exports"
JOB_PATH = "/api/v1/prep/exports/{job_id}"
ARTIFACT_PATH = "/api/v1/prep/exports/{job_id}/artifacts/{artifact_id}"
JOB_ID = "WBEXP-" + "a" * 32
SNAPSHOT_ID = "b" * 64


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


def _request() -> dict:
    return {
        "title_zh": "氧化还原主题练习",
        "subtitle_zh": "学生版与教师版",
        "duration_minutes": 20,
        "numbering_mode": "continuous_across_paper",
        "score_per_atomic": 2,
        "answer_space_lines": 3,
        "selections": [
            {
                "scope": "master",
                "selection_unit": "dependency",
                "theme_id": "THEME-1",
                "target_atomic_id": "ATOMIC-2",
                "expected_data_snapshot_id": SNAPSHOT_ID,
            }
        ],
    }


def _artifact(artifact_id: str) -> dict:
    audience, file_format = artifact_id.split("_", 1)
    return {
        "artifact_id": artifact_id,
        "format": file_format,
        "audience": audience,
        "filename": f"{audience}.{file_format}",
        "sha256": "d" * 64,
        "size_bytes": 1024,
        "page_count": 2,
        "download_path": f"{START_PATH}/{JOB_ID}/artifacts/{artifact_id}",
    }


def _job(status: str) -> dict:
    value = {
        "schema_version": "shchem.paper-export-workbench-job.v1",
        "job_id": JOB_ID,
        "status": status,
        "created_at": "2026-08-27T08:30:00.123456Z",
        "updated_at": "2026-08-27T08:30:01Z",
        "scope": "master",
        "data_snapshot_id": SNAPSHOT_ID,
        "title_zh": "氧化还原主题练习",
        "request_digest": "c" * 64,
        "progress": {
            "stage": "queued",
            "percent": 0,
            "message_zh": "已进入本机导出队列。",
        },
        "counts": {},
        "artifacts": [],
        "error": None,
    }
    if status == "completed":
        value["progress"] = {
            "stage": "completed",
            "percent": 100,
            "message_zh": "学生版与教师版 DOCX/PDF 已生成，可直接下载。",
        }
        value["counts"] = {
            "theme_count": 1,
            "printed_question_count": 2,
            "atomic_part_count": 2,
            "shared_material_count": 1,
            "total_score": 4.0,
        }
        value["artifacts"] = [
            _artifact(artifact_id)
            for artifact_id in (
                "student_docx",
                "student_pdf",
                "teacher_docx",
                "teacher_pdf",
            )
        ]
    elif status == "failed":
        value["progress"] = {
            "stage": "failed",
            "percent": 100,
            "message_zh": "题库快照已经变化。",
        }
        value["error"] = {
            "code": "theme_snapshot_stale",
            "message_zh": "题库快照已经变化。",
            "http_status": 409,
            "details": {"scope": "master"},
        }
    return value


def _envelope(data: dict) -> dict:
    return {
        "contract_version": "shchem.gateway.v1",
        "request_id": "req-paper-export-openapi",
        "data": data,
    }


def test_contract_registers_exact_three_export_paths_methods_and_bearer_auth() -> None:
    document = _contract()
    export_paths = {
        path: item
        for path, item in document["paths"].items()
        if path.startswith(START_PATH)
    }
    assert set(export_paths) == {START_PATH, JOB_PATH, ARTIFACT_PATH}
    assert set(export_paths[START_PATH]) == {"post"}
    assert set(export_paths[JOB_PATH]) == {"get"}
    assert set(export_paths[ARTIFACT_PATH]) == {"get"}

    for path, method in (
        (START_PATH, "post"),
        (JOB_PATH, "get"),
        (ARTIFACT_PATH, "get"),
    ):
        operation = export_paths[path][method]
        assert operation["security"] == [{"bearerAuth": []}]
        assert {"401", "403"}.issubset(operation["responses"])
        description = operation["description"].casefold()
        assert "teacher" in description
        assert "loopback" in description

    start = export_paths[START_PATH]["post"]
    assert start["requestBody"]["required"] is True
    assert start["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PaperExportStartRequest"
    }
    assert start["responses"]["202"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/PaperExportJobEnvelope"}
    assert export_paths[JOB_PATH]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/PaperExportJobEnvelope"}


def test_start_request_schema_is_strict_chinese_theme_first_and_integer_scored() -> None:
    document = _contract()
    for schema_name in (
        "PaperExportSelection",
        "PaperExportStartRequest",
        "PaperExportProgress",
        "PaperExportCounts",
        "PaperExportArtifact",
        "PaperExportError",
        "PaperExportJobData",
        "PaperExportJobEnvelope",
    ):
        Draft202012Validator.check_schema(document["components"]["schemas"][schema_name])

    request = _request()
    _assert_valid(document, "PaperExportStartRequest", request)
    properties = document["components"]["schemas"]["PaperExportStartRequest"][
        "properties"
    ]
    assert {"title_zh", "subtitle_zh"}.issubset(properties)
    assert properties["score_per_atomic"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 20,
        "default": 1,
    }

    mutations = []
    fractional = deepcopy(request)
    fractional["score_per_atomic"] = 0.5
    mutations.append(fractional)
    client_path = deepcopy(request)
    client_path["output_path"] = "C:/forbidden"
    mutations.append(client_path)
    theme_with_target = deepcopy(request)
    theme_with_target["selections"][0].update(
        {"selection_unit": "theme", "target_atomic_id": "ATOMIC-2"}
    )
    mutations.append(theme_with_target)
    atomic_without_target = deepcopy(request)
    atomic_without_target["selections"][0].update(
        {"selection_unit": "atomic", "target_atomic_id": None}
    )
    mutations.append(atomic_without_target)
    bad_snapshot = deepcopy(request)
    bad_snapshot["selections"][0]["expected_data_snapshot_id"] = "stale"
    mutations.append(bad_snapshot)

    validator = _validator(document, "PaperExportStartRequest")
    for mutation in mutations:
        assert list(validator.iter_errors(mutation)), mutation


def test_job_envelope_covers_all_states_and_requires_exact_four_artifacts() -> None:
    document = _contract()
    status_enum = document["components"]["schemas"]["PaperExportJobData"][
        "properties"
    ]["status"]["enum"]
    assert status_enum == ["queued", "running", "completed", "failed"]
    artifact_enum = document["components"]["schemas"]["PaperExportArtifactId"][
        "enum"
    ]
    assert artifact_enum == [
        "student_docx",
        "student_pdf",
        "teacher_docx",
        "teacher_pdf",
    ]

    for status in ("queued", "completed", "failed"):
        _assert_valid(document, "PaperExportJobEnvelope", _envelope(_job(status)))

    completed = _envelope(_job("completed"))
    missing_artifact = deepcopy(completed)
    missing_artifact["data"]["artifacts"].pop()
    assert list(
        _validator(document, "PaperExportJobEnvelope").iter_errors(missing_artifact)
    )
    mismatched_artifact = deepcopy(completed)
    mismatched_artifact["data"]["artifacts"][0]["audience"] = "teacher"
    assert list(
        _validator(document, "PaperExportJobEnvelope").iter_errors(
            mismatched_artifact
        )
    )
    failed_without_error = _envelope(_job("failed"))
    failed_without_error["data"]["error"] = None
    assert list(
        _validator(document, "PaperExportJobEnvelope").iter_errors(
            failed_without_error
        )
    )
    leaked_private_path = _envelope(_job("queued"))
    leaked_private_path["data"]["private_paths"] = {"output_root": "C:/forbidden"}
    assert list(
        _validator(document, "PaperExportJobEnvelope").iter_errors(
            leaked_private_path
        )
    )


def test_artifact_download_contract_has_exact_enum_binary_mime_and_attachment_header() -> (
    None
):
    document = _contract()
    operation = document["paths"][ARTIFACT_PATH]["get"]
    parameters = {item["name"]: item for item in operation["parameters"]}
    assert set(parameters) == {"job_id", "artifact_id"}
    assert parameters["job_id"] == {
        "in": "path",
        "name": "job_id",
        "required": True,
        "schema": {"$ref": "#/components/schemas/PaperExportJobId"},
    }
    assert parameters["artifact_id"]["schema"] == {
        "$ref": "#/components/schemas/PaperExportArtifactId"
    }

    response = operation["responses"]["200"]
    assert set(response["content"]) == {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/pdf",
    }
    for media in response["content"].values():
        assert media["schema"] == {"type": "string", "format": "binary"}
    assert response["headers"]["Content-Disposition"]["schema"] == {
        "type": "string",
        "pattern": "^attachment;",
    }
    assert "application/octet-stream" not in response["content"]
