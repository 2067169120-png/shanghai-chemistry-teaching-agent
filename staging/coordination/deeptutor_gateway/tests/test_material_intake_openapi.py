from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from integrations.deeptutor_shchem_v1.material_intake_workbench import (
    MaterialIntakeWorkbenchReader,
)
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    build_config,
    request,
    running_server,
)

WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
OPENAPI = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
)
INTAKE_PATHS = (
    "/api/v1/intake/status",
    "/api/v1/intake/batches",
    "/api/v1/intake/batches/{batch_id}",
    "/api/v1/intake/records",
)


def contract() -> dict:
    return yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))


def validator(document: dict, schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(
        {
            "$ref": f"#/components/schemas/{schema_name}",
            "components": document["components"],
        }
    )


def envelope(data: dict) -> dict:
    return {
        "contract_version": "shchem.gateway.v1",
        "request_id": "req-material-intake-openapi",
        "data": data,
    }


def assert_valid(document: dict, schema_name: str, value: dict) -> None:
    errors = list(validator(document, schema_name).iter_errors(value))
    assert errors == [], [
        {"path": list(error.absolute_path), "message": error.message}
        for error in errors[:20]
    ]


def browser_headers(server) -> dict[str, str]:
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    return {"Origin": origin, "Sec-Fetch-Site": "same-origin"}


def assert_no_locator_or_body_fields(value: object) -> None:
    forbidden = {
        "path",
        "local_path",
        "source_path",
        "absolute_path",
        "manifest_path",
        "url",
        "source_url",
        "question_text",
        "answer_text",
        "source_body",
        "content_body",
    }
    if isinstance(value, dict):
        assert forbidden.isdisjoint(value)
        for child in value.values():
            assert_no_locator_or_body_fields(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_locator_or_body_fields(child)


def test_openapi_121_preserves_four_read_only_baseline_routes() -> None:
    document = contract()
    assert document["openapi"] == "3.1.0"
    assert document["info"]["version"] == "1.24.0"

    actual = tuple(
        path for path in document["paths"] if path in INTAKE_PATHS
    )
    assert actual == INTAKE_PATHS
    for path in INTAKE_PATHS:
        item = document["paths"][path]
        assert set(item) == {"get"}
        operation = item["get"]
        assert operation["security"] == [{"bearerAuth": []}]
        description = operation["description"].casefold()
        assert "teacher" in description
        assert "loopback" in description
        assert "path" in description
        assert "url" in description
        assert "text" in description or "body" in description
        assert "requestBody" not in operation
        assert "200" in operation["responses"]
        assert "401" in operation["responses"]
        assert "403" in operation["responses"]

    parameters = {
        item["name"]: item
        for item in document["paths"]["/api/v1/intake/records"]["get"]["parameters"]
    }
    assert set(parameters) == {"kind", "stage", "status", "q", "limit", "offset"}
    schemas = document["components"]["schemas"]
    assert parameters["kind"]["schema"] == {
        "$ref": "#/components/schemas/MaterialIntakeRecordKind"
    }
    assert parameters["stage"]["schema"] == {
        "$ref": "#/components/schemas/MaterialIntakeStage"
    }
    assert parameters["status"]["schema"] == {
        "$ref": "#/components/schemas/MaterialIntakeRecordStatus"
    }
    assert schemas["MaterialIntakeCounts"]["additionalProperties"] is False
    assert schemas["MaterialIntakeCounts"]["properties"][
        "ole_objects_individually_indexed"
    ] == {"const": 0}
    assert schemas["MaterialIntakeCounts"]["properties"]["page_state_records"] == {
        "const": 0
    }


def test_real_reader_projections_validate_every_strict_response_schema() -> None:
    document = contract()
    reader = MaterialIntakeWorkbenchReader(SHCHEM_ROOT)

    status = reader.status()
    batches = reader.list_batches()
    assert_valid(document, "MaterialIntakeStatusEnvelope", envelope(status))
    assert_valid(document, "MaterialIntakeBatchListEnvelope", envelope(batches))
    assert_no_locator_or_body_fields(status)
    assert_no_locator_or_body_fields(batches)

    for item in batches["items"]:
        detail = reader.batch_detail(item["batch_id"])
        assert_valid(document, "MaterialIntakeBatchDetailEnvelope", envelope(detail))
        assert_no_locator_or_body_fields(detail)

    for kind in (
        None,
        "catalog_source",
        "paper_processing_view",
        "teaching_package",
        "teaching_document",
    ):
        records = reader.list_records(
            kind=kind,
            stage=None,
            status=None,
            query=None,
            limit=200,
            offset=0,
        )
        assert_valid(document, "MaterialIntakeRecordListEnvelope", envelope(records))
        assert_no_locator_or_body_fields(records)


def test_real_http_responses_validate_and_enforce_teacher_loopback_origin() -> None:
    document = contract()
    with (
        tempfile.TemporaryDirectory() as temp_name,
        running_server(build_config(Path(temp_name))) as server,
    ):
        headers = browser_headers(server)
        status, body, _ = request(
            server, "GET", "/api/v1/intake/status", headers=headers, timeout=30
        )
        assert status == 200
        assert_valid(document, "MaterialIntakeStatusEnvelope", body)

        status, batch_body, _ = request(
            server, "GET", "/api/v1/intake/batches", headers=headers, timeout=30
        )
        assert status == 200
        assert_valid(document, "MaterialIntakeBatchListEnvelope", batch_body)
        batch_id = batch_body["data"]["items"][0]["batch_id"]

        status, detail_body, _ = request(
            server,
            "GET",
            f"/api/v1/intake/batches/{batch_id}",
            headers=headers,
            timeout=30,
        )
        assert status == 200
        assert_valid(document, "MaterialIntakeBatchDetailEnvelope", detail_body)

        status, records_body, _ = request(
            server,
            "GET",
            "/api/v1/intake/records?kind=teaching_document&stage=registered&limit=7",
            headers=headers,
            timeout=30,
        )
        assert status == 200
        assert_valid(document, "MaterialIntakeRecordListEnvelope", records_body)
        assert records_body["data"]["count"] == 7

        status, body, _ = request(
            server, "GET", "/api/v1/intake/status", token=None, headers=headers
        )
        assert status == 401
        assert body["error"]["code"] == "authentication_required"
        status, body, _ = request(server, "GET", "/api/v1/intake/status")
        assert status == 403
        assert body["error"]["code"] == "origin_required"
        status, body, _ = request(
            server,
            "POST",
            "/api/v1/intake/batches",
            headers=headers,
            payload={"source_path": "C:/forbidden/material.docx"},
        )
        assert status == 404
        assert body["error"]["code"] == "route_not_found"

    for value in (batch_body, detail_body, records_body):
        assert_no_locator_or_body_fields(value)
        serialized = json.dumps(value, ensure_ascii=False).casefold()
        assert "c:\\users" not in serialized
        assert ".docx" not in serialized
        assert "http://" not in serialized
        assert "https://" not in serialized


def test_strict_schemas_reject_extra_fields_illegal_enums_and_authority_escalation() -> (
    None
):
    document = contract()
    reader = MaterialIntakeWorkbenchReader(SHCHEM_ROOT)
    status_value = envelope(reader.status())
    records_value = envelope(
        reader.list_records(
            kind="teaching_document",
            stage=None,
            status=None,
            query=None,
            limit=1,
            offset=0,
        )
    )

    mutations: list[tuple[str, dict]] = []
    extra = copy.deepcopy(status_value)
    extra["data"]["counts"]["unverified_total"] = 1
    mutations.append(("MaterialIntakeStatusEnvelope", extra))

    illegal_stage = copy.deepcopy(records_value)
    illegal_stage["data"]["items"][0]["ingest_stage"] = "auto_ingested"
    mutations.append(("MaterialIntakeRecordListEnvelope", illegal_stage))

    illegal_kind = copy.deepcopy(records_value)
    illegal_kind["data"]["items"][0]["record_kind"] = "raw_question_body"
    mutations.append(("MaterialIntakeRecordListEnvelope", illegal_kind))

    leaked_path = copy.deepcopy(records_value)
    leaked_path["data"]["items"][0]["source_path"] = "C:/forbidden/material.docx"
    mutations.append(("MaterialIntakeRecordListEnvelope", leaked_path))

    escalated = copy.deepcopy(records_value)
    escalated["data"]["items"][0]["authority_gates"]["recommendation_allowed"] = True
    mutations.append(("MaterialIntakeRecordListEnvelope", escalated))

    root_escalated = copy.deepcopy(status_value)
    root_escalated["data"]["authority"]["human_reviewed"] = True
    mutations.append(("MaterialIntakeStatusEnvelope", root_escalated))

    for schema_name, value in mutations:
        assert list(validator(document, schema_name).iter_errors(value)), schema_name
