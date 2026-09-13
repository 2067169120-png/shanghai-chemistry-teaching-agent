from __future__ import annotations

import copy
import tempfile
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from integrations.deeptutor_shchem_v1.curriculum_workbench import (
    CurriculumWorkbenchReader,
)
from integrations.deeptutor_shchem_v1.question_search_workbench import (
    QuestionSearchWorkbench,
)
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    build_config,
    request,
    running_server,
)
from staging.coordination.deeptutor_gateway.tests.test_question_search_workbench import (
    _FrozenThemes,
    _NoLiveFallback,
    browser_headers,
    curriculum_projection,
    theme_projection,
)

WORKSPACE = Path(__file__).resolve().parents[4]
OPENAPI = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
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


def envelope(value: dict) -> dict:
    return {
        "contract_version": "shchem.gateway.v1",
        "request_id": "req-question-search-openapi",
        "data": value,
    }


def assert_valid(document: dict, schema_name: str, value: dict) -> None:
    errors = list(validator(document, schema_name).iter_errors(value))
    assert errors == [], [
        {"path": list(error.absolute_path), "message": error.message}
        for error in errors[:20]
    ]


def test_contract_exposes_one_read_only_theme_first_post() -> None:
    document = contract()
    operation_group = document["paths"]["/api/v1/questions/search"]
    assert set(operation_group) == {"post"}
    operation = operation_group["post"]
    assert operation["security"] == [{"bearerAuth": []}]
    assert operation["requestBody"]["required"] is True
    description = operation["description"].casefold()
    for phrase in (
        "read-only",
        "teacher",
        "loopback",
        "complete theme",
        "frozen release",
        "no live fallback",
        "answer text",
        "local paths",
        "student data",
        "curriculum",
    ):
        assert phrase in description
    assert operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/QuestionSearchEnvelope"}


def test_request_and_response_are_bound_by_strict_draft_2020_schemas() -> None:
    document = contract()
    request_value = {
        "scope": "master",
        "q": "原电池",
        "filters": {
            "K": ["K10"],
            "A": ["A03"],
            "answer_status": ["present_part_aligned"],
        },
        "curriculum": {"volume_id": "TB-E1", "mapping_status": "complete"},
        "limit": 10,
    }
    assert_valid(document, "QuestionSearchRequest", request_value)
    response = QuestionSearchWorkbench().search(
        request_value,
        theme_loader=lambda scope: theme_projection(),
        curriculum_loader=lambda selector: curriculum_projection(),
        snapshot_id="e" * 64,
    )
    assert_valid(document, "QuestionSearchEnvelope", envelope(response))

    maximal_reason_request = {
        "scope": "master",
        "q": "原电池",
        "filters": {
            "K": ["K10"],
            "A": ["A03"],
            "C": ["C03"],
            "R": ["R05"],
            "RP": ["RP02"],
            "D": ["D3"],
            "item_type": ["reasoned_explanation"],
            "answer_status": ["present_part_aligned"],
            "source_tier": ["unknown"],
            "year": ["unknown"],
            "region": ["unknown"],
        },
        "curriculum": {},
    }
    maximal_reason_response = QuestionSearchWorkbench().search(
        maximal_reason_request,
        theme_loader=lambda scope: theme_projection(),
        curriculum_loader=lambda selector: curriculum_projection(),
        snapshot_id="f" * 64,
    )
    assert len(
        maximal_reason_response["items"][0]["match_details"][0]["reason_codes"]
    ) == 13
    assert_valid(
        document,
        "QuestionSearchEnvelope",
        envelope(maximal_reason_response),
    )

    bad_request = copy.deepcopy(request_value)
    bad_request["source_path"] = "C:/forbidden"
    assert list(
        validator(document, "QuestionSearchRequest").iter_errors(bad_request)
    )
    duplicate_filter = copy.deepcopy(request_value)
    duplicate_filter["filters"]["K"] = ["K10", "K10"]
    assert list(
        validator(document, "QuestionSearchRequest").iter_errors(duplicate_filter)
    )
    bad_curriculum = copy.deepcopy(request_value)
    bad_curriculum["curriculum"]["knowledge_tag"] = "K10"
    assert list(
        validator(document, "QuestionSearchRequest").iter_errors(bad_curriculum)
    )
    bad_response = envelope(response)
    bad_response["data"]["items"][0]["answer_text"] = "forbidden"
    assert list(
        validator(document, "QuestionSearchEnvelope").iter_errors(bad_response)
    )


def test_http_response_validates_against_the_contract() -> None:
    document = contract()
    with tempfile.TemporaryDirectory() as temp_name:
        config = build_config(Path(temp_name))
        with running_server(config) as server:
            server.service.frozen_browse = _FrozenThemes(theme_projection())
            server.service.workbench_product_registry = _NoLiveFallback()
            server.service.theme_workbench = _NoLiveFallback()
            status, body, _ = request(
                server,
                "POST",
                "/api/v1/questions/search",
                headers=browser_headers(server),
                payload={
                    "scope": "master",
                    "filters": {"D": ["D3"]},
                    "limit": 1,
                },
            )
    assert status == 200
    assert_valid(document, "QuestionSearchEnvelope", body)


def test_textbook_catalog_contract_and_frozen_http_route_are_strict() -> None:
    document = contract()
    operation = document["paths"]["/api/v1/textbooks"]["get"]
    assert operation["security"] == [{"bearerAuth": []}]
    description = operation["description"]
    assert "五册、十九章、六十节" in description
    assert "绝不由 K 标签反推" in description
    catalog = CurriculumWorkbenchReader(WORKSPACE / "sh-chem-db").catalog()
    assert_valid(document, "TextbookCatalogEnvelope", envelope(catalog))

    with tempfile.TemporaryDirectory() as temp_name:
        config = build_config(Path(temp_name))
        with running_server(config) as server:
            frozen = _FrozenThemes(theme_projection())
            frozen.curriculum_catalog = lambda: copy.deepcopy(catalog)
            server.service.frozen_browse = frozen
            server.service.curriculum_workbench = _NoLiveFallback()
            status, body, _ = request(
                server,
                "GET",
                "/api/v1/textbooks",
                headers=browser_headers(server),
            )
            assert status == 200
            assert_valid(document, "TextbookCatalogEnvelope", body)
            assert body["data"]["counts"]["volumes"] == 5
            assert not (config.state_root / "audit" / "gateway.jsonl").exists()

            status, body, _ = request(
                server,
                "GET",
                "/api/v1/textbooks?scope=master",
                headers=browser_headers(server),
            )
            assert status == 400
            assert body["error"]["code"] == "textbook_catalog_query_unsupported"

            class _LegacyFrozen:
                def theme_groups(self, scope: str) -> dict:
                    return theme_projection(scope)

            server.service.frozen_browse = _LegacyFrozen()
            status, body, _ = request(
                server,
                "GET",
                "/api/v1/textbooks",
                headers=browser_headers(server),
            )
            assert status == 503
            assert body["error"]["code"] == "browse_snapshot_runtime_incompatible"
