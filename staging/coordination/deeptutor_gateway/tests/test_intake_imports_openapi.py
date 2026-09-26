from __future__ import annotations

import copy
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from staging.coordination.deeptutor_gateway.tests.test_intake_imports_api import (
    SECRET,
    create_import,
    request,
    running_server,
)

WORKSPACE = Path(__file__).resolve().parents[4]
OPENAPI = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
)
HTTP_METHODS = frozenset(
    {"get", "put", "post", "delete", "patch", "options", "head", "trace"}
)
IMPORT_OPERATIONS = {
    "/api/v1/intake/imports": ("post", ("intake_import_write",)),
    "/api/v1/intake/imports/{import_id}/source": (
        "put",
        ("intake_import_write",),
    ),
    "/api/v1/intake/imports/{import_id}/analyze": (
        "post",
        ("intake_import_write", "intake_visual_execute"),
    ),
    "/api/v1/intake/imports/{import_id}/review": (
        "post",
        ("intake_import_write",),
    ),
    "/api/v1/intake/imports/{import_id}/pages/{page}/content": ("get", ()),
    "/api/v1/intake/personal-library": ("get", ()),
    "/api/v1/intake/imports/{import_id}": ("get", ()),
    "/api/v1/intake/imports/{import_id}/cancel": (
        "post",
        ("intake_import_write",),
    ),
}
READ_ONLY_BASELINE_PATHS = (
    "/api/v1/intake/status",
    "/api/v1/intake/batches",
    "/api/v1/intake/batches/{batch_id}",
    "/api/v1/intake/records",
)


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


def assert_valid(
    document: dict[str, Any], schema_name: str, instance: object
) -> None:
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


def _create_body(raw: bytes) -> dict[str, Any]:
    return {
        "source_role": "question_paper",
        "year": "unknown",
        "region_or_school": "unknown",
        "paper_type": "unknown",
        "filename": "openapi-contract.png",
        "mime_type": "image/png",
        "size_bytes": len(raw),
    }


@pytest.fixture(scope="module")
def live_contract_samples(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    document = contract()
    tmp_path = tmp_path_factory.mktemp("intake-imports-openapi")
    with running_server(tmp_path) as (server, _transport, intake_root):
        cancel_import_id, raw, _cancel_created = create_import(server)

        create_body = _create_body(raw)
        assert_valid(document, "IntakeImportCreateRequest", create_body)
        status, created, _headers = request(
            server,
            "POST",
            "/api/v1/intake/imports",
            json_body=create_body,
        )
        assert status == 201
        assert_valid(document, "IntakeImportEnvelope", created)
        import_id = created["data"]["import_id"]

        status, uploaded, _headers = request(
            server,
            "PUT",
            f"/api/v1/intake/imports/{import_id}/source",
            raw_body=raw,
            content_type="image/png",
        )
        assert status == 200
        assert_valid(document, "IntakeImportEnvelope", uploaded)

        analyze_body = {
            "profile_id": "vision-profile",
            "expected_revision": server.intake_profile["revision"],
            "teacher_confirmed_egress": True,
        }
        assert_valid(document, "IntakeImportAnalyzeRequest", analyze_body)
        status, analyzing, _headers = request(
            server,
            "POST",
            f"/api/v1/intake/imports/{import_id}/analyze",
            json_body=analyze_body,
        )
        assert status == 202
        assert_valid(document, "IntakeImportEnvelope", analyzing)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, fetched, _headers = request(
                server, "GET", f"/api/v1/intake/imports/{import_id}"
            )
            assert status == 200
            assert_valid(document, "IntakeImportEnvelope", fetched)
            if fetched["data"]["status"] == "completed":
                break
            time.sleep(0.01)
        else:
            raise AssertionError("HTTP intake analysis did not complete")

        review_body = {
            "expected_review_revision": 0,
            "candidate_sha256": fetched["data"]["candidate_sha256"],
            "decision": "accept_personal_library",
            "acknowledged_blocker_codes": ["unreadable_visual"],
            "teacher_note_zh": "已核对原页与结构候选。",
        }
        assert_valid(document, "IntakeImportReviewRequest", review_body)
        status, reviewed, _headers = request(
            server,
            "POST",
            f"/api/v1/intake/imports/{import_id}/review",
            json_body=review_body,
        )
        assert status == 200
        assert_valid(document, "IntakeImportEnvelope", reviewed)

        status, personal_library, _headers = request(
            server, "GET", "/api/v1/intake/personal-library"
        )
        assert status == 200
        assert_valid(document, "IntakePersonalLibraryEnvelope", personal_library)

        cancel_body: dict[str, Any] = {}
        assert_valid(document, "IntakeImportCancelRequest", cancel_body)
        status, cancelled, _headers = request(
            server,
            "POST",
            f"/api/v1/intake/imports/{cancel_import_id}/cancel",
            json_body=cancel_body,
        )
        assert status == 200
        assert cancelled["data"]["status"] == "cancelled"
        assert_valid(document, "IntakeImportEnvelope", cancelled)

        return {
            "create_request": create_body,
            "analyze_request": analyze_body,
            "create": created,
            "upload": uploaded,
            "analyze": analyzing,
            "get": fetched,
            "review_request": review_body,
            "review": reviewed,
            "personal_library": personal_library,
            "cancel": cancelled,
            "intake_root": str(intake_root),
        }


def test_openapi_121_declares_import_security_and_preserves_baseline_gets() -> None:
    document = contract()
    assert document["openapi"] == "3.1.0"
    assert document["info"]["version"] == "1.24.0"

    for path, (method, capabilities) in IMPORT_OPERATIONS.items():
        path_item = document["paths"][path]
        assert set(path_item).intersection(HTTP_METHODS) == {method}
        operation = path_item[method]
        assert operation["security"] == [{"bearerAuth": []}]
        description = operation["description"].casefold()
        assert "teacher" in description
        assert "origin" in description
        assert "loopback" in description
        for capability in capabilities:
            assert capability in description
        assert "200" in operation["responses"] or "201" in operation["responses"]
        assert "401" in operation["responses"]
        assert "403" in operation["responses"]

    for path in READ_ONLY_BASELINE_PATHS:
        path_item = document["paths"][path]
        assert set(path_item) == {"get"}
        assert "requestBody" not in path_item["get"]


def test_intake_schema_objects_are_closed_and_all_contract_refs_resolve() -> None:
    document = contract()
    schemas = document["components"]["schemas"]
    intake_schemas = {
        name: schema for name, schema in schemas.items() if name.startswith("Intake")
    }
    assert "IntakeImportEnvelope" in intake_schemas

    for name, schema in intake_schemas.items():
        Draft202012Validator.check_schema(schema)
        for path, object_schema in iter_object_schemas(schema, (name,)):
            assert object_schema.get("additionalProperties") is False, "/".join(path)

    refs = set(iter_refs(document))
    assert refs
    for ref in sorted(refs):
        resolve_local_ref(document, ref)


def test_real_http_create_upload_analyze_get_and_cancel_envelopes_validate(
    live_contract_samples: dict[str, Any],
) -> None:
    document = contract()
    for operation in ("create", "upload", "analyze", "get", "review", "cancel"):
        assert_valid(
            document,
            "IntakeImportEnvelope",
            live_contract_samples[operation],
        )

    assert live_contract_samples["create"]["data"]["status"] == "awaiting_upload"
    assert (
        live_contract_samples["upload"]["data"]["status"]
        == "ready_for_analysis"
    )
    assert live_contract_samples["get"]["data"]["status"] == "completed"
    assert (
        live_contract_samples["review"]["data"]["review"]["status"]
        == "accepted_personal_library"
    )
    assert live_contract_samples["personal_library"]["data"]["count"] == 1
    assert live_contract_samples["cancel"]["data"]["status"] == "cancelled"


def test_strict_contract_rejects_extras_path_or_key_leaks_and_false_safety_facts(
    live_contract_samples: dict[str, Any],
) -> None:
    document = contract()
    envelope_validator = schema_validator(document, "IntakeImportEnvelope")
    completed = live_contract_samples["get"]

    serialized_samples = json.dumps(live_contract_samples, ensure_ascii=False)
    assert SECRET not in serialized_samples
    assert live_contract_samples["intake_root"] not in json.dumps(
        {key: value for key, value in live_contract_samples.items() if key != "intake_root"},
        ensure_ascii=False,
    )

    invalid_envelopes: list[tuple[str, dict[str, Any]]] = []

    extra_field = copy.deepcopy(completed)
    extra_field["data"]["unexpected"] = True
    invalid_envelopes.append(("additional response field", extra_field))

    path_leak = copy.deepcopy(completed)
    path_leak["data"]["source"]["local_path"] = r"C:\private\source.png"
    invalid_envelopes.append(("local path leakage", path_leak))

    key_leak = copy.deepcopy(completed)
    key_leak["data"]["attempts"][-1]["api_key"] = "sk-do-not-return"
    invalid_envelopes.append(("provider Key leakage", key_leak))

    central_write = copy.deepcopy(completed)
    central_write["data"]["central_registry_write"] = True
    invalid_envelopes.append(("job central_registry_write", central_write))

    candidate_write = copy.deepcopy(completed)
    candidate_write["data"]["candidate"]["central_registry_write"] = True
    invalid_envelopes.append(("candidate central_registry_write", candidate_write))

    no_teacher_review = copy.deepcopy(completed)
    no_teacher_review["data"]["candidate"]["requires_teacher_review"] = False
    invalid_envelopes.append(("requires_teacher_review", no_teacher_review))

    string_model_invoked = copy.deepcopy(completed)
    string_model_invoked["data"]["attempts"][-1]["model_invoked"] = "true"
    invalid_envelopes.append(("model_invoked type", string_model_invoked))

    for label, instance in invalid_envelopes:
        assert not envelope_validator.is_valid(instance), label

    create_with_path = {
        **live_contract_samples["create_request"],
        "local_path": r"C:\private\source.png",
    }
    create_with_key = {
        **live_contract_samples["create_request"],
        "api_key": "sk-do-not-accept",
    }
    analyze_with_key = {
        **live_contract_samples["analyze_request"],
        "api_key": "sk-do-not-accept",
    }
    assert not schema_validator(document, "IntakeImportCreateRequest").is_valid(
        create_with_path
    )
    assert not schema_validator(document, "IntakeImportCreateRequest").is_valid(
        create_with_key
    )
    assert not schema_validator(document, "IntakeImportAnalyzeRequest").is_valid(
        analyze_with_key
    )
    assert not schema_validator(document, "IntakeImportCancelRequest").is_valid(
        {"reason": "extra fields are closed"}
    )
