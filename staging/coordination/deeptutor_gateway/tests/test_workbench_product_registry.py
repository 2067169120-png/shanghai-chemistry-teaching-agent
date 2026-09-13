from __future__ import annotations

import copy
import hashlib
import importlib
import json
import re
import tempfile
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from integrations.deeptutor_shchem_v1.config import Principal, token_digest
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    build_config,
    request,
    running_server,
)


WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
OVERLAY_MANIFEST = OVERLAY / "overlay.manifest.json"
OPENAPI = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
)

API_CONTRACT_VERSION = "shchem.gateway.v1"
REGISTRY_SCHEMA_VERSION = "1.0.0-workbench-product-registry"
READINESS_SCHEMA_VERSION = "1.0.0-workbench-readiness"
REGISTRY_ID = "SHCHEM-WORKBENCH-PRODUCT-REGISTRY-V1"
PRODUCT_ORDER = ("wave1", "master", "supplemental")
DATA_SNAPSHOT_ALGORITHM = (
    "canonical-sha256-of-workbench-product-projections-v1"
)
STUDENT_TOKEN = "workbench-registry-student-0123456789"

REGISTRY_KEYS = {
    "schema_version",
    "registry_id",
    "api_contract_version",
    "ui_build_id",
    "manifest_sha256",
    "data_snapshot_id",
    "data_snapshot_algorithm",
    "product_count",
    "product_order",
    "products",
    "combined_atomic_total",
    "cross_scope_sum_allowed",
    "scope_boundary_zh",
    "authority",
    "integrity",
}
PRODUCT_KEYS = {
    "product_id",
    "display_name_zh",
    "scope",
    "schema_version",
    "status",
    "compatible",
    "data_snapshot_id",
    "manifest_sha256",
    "counts",
    "blockers",
    "non_additive_to",
}
COUNT_KEYS = {
    "papers",
    "theme_big_questions",
    "printed_questions",
    "atomic_parts",
    "display_atomic_units",
    "unassigned_atomic_parts",
}
READINESS_KEYS = {
    "schema_version",
    "status",
    "api_contract_version",
    "ui_build_id",
    "manifest_sha256",
    "data_snapshot_id",
    "product_registry_id",
    "product_count",
    "product_ids",
    "ui_contract_compatible",
    "ready_for_candidate_browse",
    "combined_atomic_total",
    "cross_scope_sum_allowed",
    "subsystem_checks",
    "blockers",
    "authority",
}
AUTHORITY = {
    "candidate_only": True,
    "read_only": True,
    "human_reviewed": False,
    "official": False,
    "retrieval_ready": False,
    "teaching_use_allowed": False,
    "generation_allowed": False,
    "publication_allowed": False,
}
INTEGRITY_FLAGS = {
    "overlay_manifest_hash_verified": True,
    "ui_file_hashes_verified": True,
    "product_manifests_verified": True,
    "product_counts_derived": True,
    "stable_product_order": True,
    "unknown_products_rejected": True,
    "duplicate_products_rejected": True,
    "incompatible_products_rejected": True,
    "question_content_exposed": False,
    "source_paths_exposed": False,
    "answer_text_exposed": False,
    "fail_closed": True,
}
FORBIDDEN_KEYS = {
    "absolute_path",
    "answer_text",
    "crop_path",
    "file_path",
    "local_path",
    "question_text",
    "reference_answer_text",
    "source_path",
    "source_url",
    "task_summary",
    "title_or_literal",
    "url",
}
FORBIDDEN_TEXT = re.compile(
    r"(?i:https?://|file:/+|[a-z]:[\\/]|\\\\[^\\/\s]+[\\/]"
    r"|(?:^|[\s\"'])(?:sh-chem-db|kb|staging|runtime|integrations)[\\/]"
    r"|\.(?:docx|pdf|png|jpg|jpeg)(?:[\"'\s]|$))"
)


def _registry_module():
    return importlib.import_module(
        "integrations.deeptutor_shchem_v1.workbench_product_registry"
    )


def _reader():
    module = _registry_module()
    return module.WorkbenchProductRegistryReader(SHCHEM_ROOT, OVERLAY)


def _canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _ui_build_id() -> str:
    manifest = json.loads(OVERLAY_MANIFEST.read_text(encoding="utf-8"))
    return _canonical_sha256(manifest["files"])


def _manifest_sha256() -> str:
    return hashlib.sha256(OVERLAY_MANIFEST.read_bytes()).hexdigest()


def _browser_headers(server) -> dict[str, str]:
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    return {"Origin": origin, "Sec-Fetch-Site": "same-origin"}


def _assert_no_content_or_locator_leak(value: object) -> None:
    def walk(current: object) -> None:
        if isinstance(current, dict):
            assert not (set(current) & FORBIDDEN_KEYS), set(current) & FORBIDDEN_KEYS
            for nested in current.values():
                walk(nested)
        elif isinstance(current, list):
            for nested in current:
                walk(nested)

    walk(value)
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    assert FORBIDDEN_TEXT.search(serialized) is None


def _assert_hex_sha256(value: object) -> None:
    assert isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)


def _assert_authority(value: object) -> None:
    assert value == AUTHORITY


def _assert_product_shape(product: dict, expected_scope: str) -> None:
    assert set(product) == PRODUCT_KEYS
    assert product["product_id"] == product["scope"] == expected_scope
    assert isinstance(product["display_name_zh"], str) and product[
        "display_name_zh"
    ].strip()
    assert isinstance(product["schema_version"], str) and product[
        "schema_version"
    ].strip()
    assert product["status"] == "ready_candidate_browse"
    assert product["compatible"] is True
    _assert_hex_sha256(product["data_snapshot_id"])
    _assert_hex_sha256(product["manifest_sha256"])
    assert set(product["counts"]) == COUNT_KEYS
    for key in COUNT_KEYS - {"printed_questions"}:
        assert type(product["counts"][key]) is int and product["counts"][key] >= 0
    printed = product["counts"]["printed_questions"]
    assert printed is None or (type(printed) is int and printed >= 0)
    assert isinstance(product["blockers"], list)
    if printed is None:
        assert any(
            blocker.get("code") == "printed_questions_not_complete_from_theme_projection"
            for blocker in product["blockers"]
            if isinstance(blocker, dict)
        )
    assert product["non_additive_to"] == [
        scope for scope in PRODUCT_ORDER if scope != expected_scope
    ]


def _assert_registry_shape(value: dict) -> None:
    assert set(value) == REGISTRY_KEYS
    assert value["schema_version"] == REGISTRY_SCHEMA_VERSION
    assert value["registry_id"] == REGISTRY_ID
    assert value["api_contract_version"] == API_CONTRACT_VERSION
    assert value["ui_build_id"] == _ui_build_id()
    assert value["manifest_sha256"] == _manifest_sha256()
    _assert_hex_sha256(value["data_snapshot_id"])
    assert value["data_snapshot_algorithm"] == DATA_SNAPSHOT_ALGORITHM
    assert value["product_count"] == len(value["products"]) == 3
    assert value["product_order"] == list(PRODUCT_ORDER)
    assert [product["product_id"] for product in value["products"]] == list(
        PRODUCT_ORDER
    )
    for scope, product in zip(PRODUCT_ORDER, value["products"], strict=True):
        _assert_product_shape(product, scope)
    expected_snapshot = _canonical_sha256(
        {
            "algorithm": DATA_SNAPSHOT_ALGORITHM,
            "products": value["products"],
        }
    )
    assert value["data_snapshot_id"] == expected_snapshot
    assert value["combined_atomic_total"] is None
    assert value["cross_scope_sum_allowed"] is False
    assert isinstance(value["scope_boundary_zh"], str)
    assert "不能相加" in value["scope_boundary_zh"]
    _assert_authority(value["authority"])
    assert set(value["integrity"]) == {
        *INTEGRITY_FLAGS,
        "registry_file_sha256",
    }
    for key, expected in INTEGRITY_FLAGS.items():
        assert value["integrity"][key] is expected
    _assert_hex_sha256(value["integrity"]["registry_file_sha256"])
    _assert_no_content_or_locator_leak(value)


def test_reader_returns_three_stable_non_additive_dynamic_product_scopes():
    reader = _reader()
    first = reader.registry()
    second = reader.registry()
    assert second == first
    _assert_registry_shape(first)

    for product in first["products"]:
        theme = reader.theme_groups(product["scope"])
        counts = product["counts"]
        assert counts["papers"] == theme["counts"]["papers"]
        assert counts["theme_big_questions"] == theme["counts"]["theme_groups"]
        assert counts["atomic_parts"] == theme["counts"]["atomic_parts"]
        assert counts["display_atomic_units"] == theme["counts"][
            "display_atomic_units"
        ]
        assert counts["unassigned_atomic_parts"] == theme["counts"][
            "unassigned_atomic_parts"
        ]
        if product["scope"] == "supplemental":
            assert theme["data_snapshot_id"] == product["data_snapshot_id"]

    assert first["products"][0]["counts"]["atomic_parts"] != sum(
        product["counts"]["atomic_parts"] for product in first["products"]
    )


def test_readiness_binds_the_same_ui_contract_and_registry_snapshot():
    reader = _reader()
    registry = reader.registry()
    value = reader.readiness()
    assert set(value) == READINESS_KEYS
    assert value["schema_version"] == READINESS_SCHEMA_VERSION
    assert value["status"] == "ready_candidate_browse"
    assert value["api_contract_version"] == API_CONTRACT_VERSION
    assert value["ui_build_id"] == registry["ui_build_id"]
    assert value["manifest_sha256"] == registry["manifest_sha256"]
    assert value["data_snapshot_id"] == registry["data_snapshot_id"]
    assert value["product_registry_id"] == registry["registry_id"]
    assert value["product_count"] == registry["product_count"]
    assert value["product_ids"] == registry["product_order"]
    assert value["ui_contract_compatible"] is True
    assert value["ready_for_candidate_browse"] is True
    assert value["combined_atomic_total"] is None
    assert value["cross_scope_sum_allowed"] is False
    assert set(value["subsystem_checks"]) == {
        "product_registry",
        "overlay_static_binding",
        "offline_browse",
        "full_bank_readiness",
    }
    for name, check in value["subsystem_checks"].items():
        assert set(check) == {"status", "blockers"}, name
        assert check["status"] in {
            "pass",
            "separate_governance_not_browse_product",
        }
        assert isinstance(check["blockers"], list)
    assert value["subsystem_checks"]["product_registry"]["status"] == "pass"
    assert value["subsystem_checks"]["overlay_static_binding"]["status"] == "pass"
    assert value["subsystem_checks"]["offline_browse"]["status"] == "pass"
    assert value["subsystem_checks"]["full_bank_readiness"]["status"] == (
        "separate_governance_not_browse_product"
    )
    assert isinstance(value["blockers"], list)
    for blocker in value["blockers"]:
        assert set(blocker) == {"code", "severity", "blocks", "message_zh"}
        assert blocker["severity"] in {"info", "warning", "error"}
        assert isinstance(blocker["blocks"], list) and blocker["blocks"]
        assert "candidate_browse" not in blocker["blocks"]
        assert isinstance(blocker["message_zh"], str) and blocker["message_zh"].strip()
    model_blocker = next(
        blocker
        for blocker in value["blockers"]
        if blocker["code"] == "model_provider_not_configured"
    )
    assert model_blocker["blocks"] == ["model_analysis", "generation"]
    _assert_authority(value["authority"])
    _assert_no_content_or_locator_leak(value)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("unknown", "workbench_registry_unknown_product"),
        ("duplicate", "workbench_registry_duplicate_product"),
        ("contract", "workbench_registry_contract_incompatible"),
        ("schema", "workbench_registry_schema_incompatible"),
    ),
)
def test_reader_rejects_unknown_duplicate_or_incompatible_products(
    monkeypatch, mutation: str, expected_code: str
):
    module = _registry_module()
    reader = module.WorkbenchProductRegistryReader(SHCHEM_ROOT, OVERLAY)
    descriptors = [copy.deepcopy(value) for value in reader._product_descriptors()]
    assert all(isinstance(value, dict) for value in descriptors)

    if mutation == "unknown":
        rogue = copy.deepcopy(descriptors[0])
        rogue["product_id"] = rogue["scope"] = "rogue"
        descriptors.append(rogue)
    elif mutation == "duplicate":
        descriptors.append(copy.deepcopy(descriptors[0]))
    elif mutation == "contract":
        descriptors[0]["api_contract_version"] = "shchem.gateway.v0"
    elif mutation == "schema":
        descriptors[0]["schema_version"] = "0.0.0-incompatible"
    else:  # pragma: no cover - parametrization is closed above
        raise AssertionError(mutation)

    monkeypatch.setattr(reader, "_product_descriptors", lambda: tuple(descriptors))
    with pytest.raises(module.WorkbenchProductRegistryError) as captured:
        reader.registry()
    assert captured.value.status == 409
    assert captured.value.code == expected_code


def test_http_requires_teacher_loopback_rejects_queries_and_remains_read_only():
    with tempfile.TemporaryDirectory() as temp_name:
        config = build_config(Path(temp_name))
        config.principals.append(
            Principal(
                "workbench-registry-student",
                "student",
                token_digest(STUDENT_TOKEN),
                (),
            )
        )
        with running_server(config) as server:
            headers = _browser_headers(server)
            for route in (
                "/api/v1/readiness",
                "/api/v1/workbench/product-registry",
            ):
                status, body, _ = request(server, "GET", route, token=None)
                assert status == 401
                assert body["error"]["code"] == "authentication_required"

                status, body, _ = request(
                    server, "GET", route, token=STUDENT_TOKEN, headers=headers
                )
                assert status == 403
                assert body["error"]["code"] == "teacher_scope_required"

                status, body, _ = request(server, "GET", route)
                assert status == 403
                assert body["error"]["code"] == "origin_required"

                status, body, _ = request(
                    server,
                    "GET",
                    route,
                    headers={
                        "Origin": "https://evil.example",
                        "Sec-Fetch-Site": "cross-site",
                    },
                )
                assert status == 403
                assert body["error"]["code"] == "origin_denied"

                status, body, _ = request(
                    server, "GET", route, headers=headers, timeout=30
                )
                assert status == 200
                assert set(body) == {"contract_version", "request_id", "data"}
                assert body["contract_version"] == API_CONTRACT_VERSION
                _assert_no_content_or_locator_leak(body["data"])

                status, body, _ = request(
                    server, "GET", route + "?product_id=wave1", headers=headers
                )
                assert status == 400
                expected = (
                    "readiness_query_unsupported"
                    if route.endswith("readiness")
                    else "workbench_registry_query_unsupported"
                )
                assert body["error"]["code"] == expected

                status, body, _ = request(
                    server,
                    "POST",
                    route,
                    headers=headers,
                    payload={"product_id": "wave1"},
                )
                assert status == 404
                assert body["error"]["code"] == "route_not_found"


def test_http_order_and_snapshots_are_stable_and_openapi_validates_both_dtos():
    contract = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    assert "/api/v1/readiness" in contract["paths"]
    assert "/api/v1/workbench/product-registry" in contract["paths"]
    with tempfile.TemporaryDirectory() as temp_name, running_server(
        build_config(Path(temp_name))
    ) as server:
        headers = _browser_headers(server)
        registry_values = []
        readiness_values = []
        for _ in range(2):
            status, body, _ = request(
                server,
                "GET",
                "/api/v1/workbench/product-registry",
                headers=headers,
                timeout=30,
            )
            assert status == 200
            registry_values.append(body["data"])
            status, body, _ = request(
                server,
                "GET",
                "/api/v1/readiness",
                headers=headers,
                timeout=30,
            )
            assert status == 200
            readiness_values.append(body["data"])
        assert registry_values[0] == registry_values[1]
        assert readiness_values[0] == readiness_values[1]
        _assert_registry_shape(registry_values[0])
        assert readiness_values[0]["data_snapshot_id"] == registry_values[0][
            "data_snapshot_id"
        ]

    for schema_name, value in (
        ("WorkbenchProductRegistryData", registry_values[0]),
        ("WorkbenchReadinessData", readiness_values[0]),
    ):
        validator = Draft202012Validator(
            {
                "$ref": f"#/components/schemas/{schema_name}",
                "components": contract["components"],
            }
        )
        errors = list(validator.iter_errors(value))
        assert errors == [], [
            {
                "schema": schema_name,
                "path": list(error.absolute_path),
                "message": error.message,
            }
            for error in errors[:10]
        ]
