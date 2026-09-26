from __future__ import annotations

import hashlib
import http.client
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from jsonschema import Draft202012Validator, RefResolver

from integrations.deeptutor_shchem_v1 import master_direct_visual_scan as direct_module
from integrations.deeptutor_shchem_v1 import master_visual_scan_alias as alias_module
from integrations.deeptutor_shchem_v1.master_visual_scan_alias import (
    COVERAGE_KIND,
    EXPECTED_COUNTS,
    EXPECTED_MANIFEST_FILE_SHA256,
    EXPECTED_MANIFEST_SELF_SHA256,
    EXPECTED_MASTER_NODE_IDS,
    PRODUCT_RELATIVE,
    MasterVisualScanAliasError,
    MasterVisualScanAliasReader,
)
from integrations.deeptutor_shchem_v1.master_wave1_workbench import (
    MasterWave1WorkbenchReader,
)
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    TOKEN_A,
    build_config,
    running_server,
)

WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
PRODUCT = SHCHEM_ROOT / PRODUCT_RELATIVE
OPENAPI = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
)
ONE_TO_MANY_ID = "MASTER-PART-e2f8e8e5a5539ea91234"
THREE_TARGET_ID = "MASTER-PART-42e284d0b9dd37af22d0"
QUALITY_NOTE_ID = "PT2026-EM-S3-Q1-P1"
QUALITY_NOTE_CROP_ID = "PT2026-T3-Q01-QUESTION-S1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            _sha256(path),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _walk_keys(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from _walk_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_keys(nested)


def _browser_headers(server) -> dict[str, str]:
    return {
        "Origin": f"http://127.0.0.1:{server.server_address[1]}",
        "Sec-Fetch-Site": "same-origin",
    }


def _http_get(server, path: str, *, token: str | None = TOKEN_A):
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=60
    )
    headers = _browser_headers(server)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    connection.request("GET", path, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    response_headers = response.headers
    content_type = response.getheader("Content-Type") or ""
    connection.close()
    if "application/json" in content_type:
        return status, json.loads(raw.decode("utf-8")), response_headers
    return status, raw, response_headers


def _schema_errors(contract, schema_name: str, payload: dict):
    resolver = RefResolver.from_schema(contract)
    return list(
        Draft202012Validator(
            contract["components"]["schemas"][schema_name], resolver=resolver
        ).iter_errors(payload)
    )


@pytest.fixture(scope="module")
def validated_reader():
    reader = MasterVisualScanAliasReader(SHCHEM_ROOT)
    snapshot = reader._snapshot()
    # The first call above exercises every live hash, line, parent-chain,
    # PT52, crop/source-page, and legacy pixel invariant.  Projection tests
    # reuse that immutable in-memory result so this suite does not repeat the
    # relatively expensive 9-way LANCZOS registration for every assertion.
    with patch.object(reader, "_snapshot", return_value=snapshot):
        yield reader, snapshot


def test_frozen_manifest_file_and_self_identity_are_exact():
    manifest = json.loads((PRODUCT / "manifest.json").read_text(encoding="utf-8"))
    assert (PRODUCT / "manifest.json").stat().st_size == 3182
    assert _sha256(PRODUCT / "manifest.json") == EXPECTED_MANIFEST_FILE_SHA256
    assert manifest["manifest_self_sha256"] == EXPECTED_MANIFEST_SELF_SHA256
    assert manifest["fixed_counts"] == EXPECTED_COUNTS
    assert manifest["fixed_counts"]["new_physical_scans"] == 0
    assert set(manifest["authority_gates"].values()) == {False}


def test_live_reader_recomputes_full_closure_and_pixel_proofs(validated_reader):
    reader, snapshot = validated_reader
    status = reader.status()
    assert len(snapshot.records) == 14
    assert len(snapshot.source_bindings) == 44
    assert status["coverage_kind"] == COVERAGE_KIND
    assert status["new_physical_scans"] == 0
    assert status["counts"] == EXPECTED_COUNTS
    assert status["integrity"] == {
        "manifest_self_sha256": EXPECTED_MANIFEST_SELF_SHA256,
        "manifest_file_sha256": EXPECTED_MANIFEST_FILE_SHA256,
        "master_manifest_self_sha256": snapshot.master_manifest_self_sha256,
        "pt52_manifest_self_sha256": snapshot.pt_manifest_self_sha256,
        "output_binding_count": 9,
        "source_binding_count": 44,
        "record_count": 14,
        "hash_verified_on_read": True,
        "semantic_invariants_verified_on_read": True,
        "master_membership_verified_on_read": True,
        "exact169_disjoint_verified_on_read": True,
        "pt52_targets_verified_on_read": True,
        "legacy_pixel_metrics_recomputed_on_read": True,
        "fail_closed": True,
    }
    assert status["authority"]["candidate_only"] is True
    assert status["authority"]["read_only"] is True
    assert all(
        value is False
        for key, value in status["authority"].items()
        if key not in {"candidate_only", "read_only"}
    )


def test_catalog_has_exact_14_alias_ids_and_never_contains_answer_text(validated_reader):
    reader, _ = validated_reader
    catalog = reader.catalog()
    assert catalog["count"] == 14
    assert tuple(catalog["master_node_ids"]) == EXPECTED_MASTER_NODE_IDS
    assert sum(item["relation_type"] == "alias_coverage_1_to_1" for item in catalog["items"]) == 8
    assert sum(item["relation_type"] == "alias_coverage_1_to_many" for item in catalog["items"]) == 6
    assert {item["coverage_kind"] for item in catalog["items"]} == {COVERAGE_KIND}
    assert {item["new_physical_scans"] for item in catalog["items"]} == {0}
    assert len({item["target_printed_question_id"] for item in catalog["items"]}) == 13
    assert len(
        {
            target_id
            for item in catalog["items"]
            for target_id in item["target_atomic_part_ids"]
        }
    ) == 19
    assert all(
        item["has_quality_note"]
        == any(unit["has_quality_note"] for unit in item["reference_answer_units"])
        for item in catalog["items"]
    )
    forbidden = {
        "reference_answer_text",
        "merged_reference_answer_text",
        "reference_summary_zh",
        "answer_text",
    }
    assert forbidden.isdisjoint(set(_walk_keys(catalog)))
    assert "非官方答案整页标BD" not in json.dumps(catalog, ensure_ascii=False)


def test_one_to_one_detail_projects_original_answer_with_nonblocking_q1_note(validated_reader):
    reader, snapshot = validated_reader
    detail = reader.detail(QUALITY_NOTE_ID)
    assert detail["coverage_kind"] == COVERAGE_KIND
    assert detail["relation_type"] == "alias_coverage_1_to_1"
    assert detail["cardinality"] == "1:1"
    assert detail["new_physical_scans"] == 0
    assert len(detail["target_atomic_parts"]) == 1
    unit = detail["target_atomic_parts"][0]
    upstream = snapshot.pt_snapshot.by_node_id[unit["wave_atomic_part_id"]]
    projected = unit["reference_answer"]
    assert projected["availability"] == "present_part_aligned"
    assert projected["reference_answer_text"] == upstream["answer"]["reference_summary_zh"]
    assert projected["source_authority"] == "nonofficial_reference"
    assert projected["independently_verified"] is False
    assert "冲突" in projected["quality_note"]
    assert "非阻断" in projected["quality_note"]
    assert detail["answer_projection"]["merged_reference_answer_text"] is None
    assert detail["answer_projection"]["quality_note_nonblocking_zh"] == projected["quality_note"]


@pytest.mark.parametrize(
    ("master_id", "cardinality", "target_count"),
    ((ONE_TO_MANY_ID, "1:2", 2), (THREE_TARGET_ID, "1:3", 3)),
)
def test_one_to_many_detail_keeps_atomic_and_answer_units_separate(
    validated_reader, master_id, cardinality, target_count
):
    reader, _ = validated_reader
    detail = reader.detail(master_id)
    assert detail["relation_type"] == "alias_coverage_1_to_many"
    assert detail["cardinality"] == cardinality
    assert len(detail["target_atomic_parts"]) == target_count
    assert len(detail["target_atomic_part_ids"]) == target_count
    assert len(set(detail["target_atomic_part_ids"])) == target_count
    assert [unit["wave_atomic_part_id"] for unit in detail["target_atomic_parts"]] == detail[
        "target_atomic_part_ids"
    ]
    assert detail["answer_projection"] == {
        "merge_prohibited_for_one_to_many": True,
        "merged_reference_answer_text": None,
        "unit_count": target_count,
        "quality_note_nonblocking_zh": None,
        "independently_verified": False,
        "source_authority": "nonofficial_reference",
    }
    assert "visible_summary_zh" not in detail
    assert "scan_classification" not in detail
    for unit in detail["target_atomic_parts"]:
        assert unit["reference_answer"]["reference_answer_text"] is None
        assert unit["reference_answer"]["availability"] == "present_unaligned"
        assert unit["reference_answer"]["independently_verified"] is False


def test_question_crop_serves_only_exact_bound_question_pixels(validated_reader):
    reader, _ = validated_reader
    payload = reader.question_crop(QUALITY_NOTE_ID, QUALITY_NOTE_CROP_ID)
    assert payload.content_type == "image/png"
    assert payload.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert hashlib.sha256(payload.data).hexdigest() == payload.sha256
    assert payload.sha256 == "3e43669d4a53c05b513bed1fa11957987423084a4df71d92e0a54451038d87a1"
    with pytest.raises(MasterVisualScanAliasError) as issue:
        reader.question_crop(QUALITY_NOTE_ID, "PT2026-T3-Q01-ANSWER-S1")
    assert issue.value.status == 403
    assert issue.value.code == "master_alias_crop_role_denied"


def test_identifier_and_not_found_errors_fail_closed(validated_reader):
    reader, _ = validated_reader
    with pytest.raises(MasterVisualScanAliasError) as invalid:
        reader.detail("../manifest.json")
    assert invalid.value.status == 400
    with pytest.raises(MasterVisualScanAliasError) as missing:
        reader.detail("MASTER-PART-00000000000000000000")
    assert missing.value.status == 404


def test_catalog_detail_and_crop_are_zero_write(validated_reader):
    reader, _ = validated_reader
    before = _tree_snapshot(PRODUCT)
    reader.catalog()
    reader.detail(QUALITY_NOTE_ID)
    reader.question_crop(QUALITY_NOTE_ID, QUALITY_NOTE_CROP_ID)
    assert _tree_snapshot(PRODUCT) == before


@pytest.mark.parametrize("mutation", ("bound_bytes", "unbound_file"))
def test_product_drift_and_recursive_output_addition_fail_closed(monkeypatch, mutation):
    with tempfile.TemporaryDirectory(prefix="alias-reader-test-", dir=SHCHEM_ROOT) as raw:
        temp_root = Path(raw)
        copied = temp_root / "product"
        shutil.copytree(PRODUCT, copied)
        if mutation == "bound_bytes":
            records = copied / "alias_records.jsonl"
            records.write_bytes(records.read_bytes() + b"\n")
        else:
            (copied / "rogue.json").write_text("{}\n", encoding="utf-8")
        monkeypatch.setattr(
            alias_module,
            "PRODUCT_RELATIVE",
            copied.relative_to(SHCHEM_ROOT),
        )
        reader = MasterVisualScanAliasReader(SHCHEM_ROOT)
        with pytest.raises(MasterVisualScanAliasError) as issue:
            reader.catalog()
        assert issue.value.status == 409
        assert issue.value.code in {
            "master_alias_binding_mismatch",
            "master_alias_binding_invalid",
        }


def test_openapi_has_resolved_strict_alias_and_unified_master_schemas(validated_reader):
    reader, _ = validated_reader
    contract = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    paths = contract["paths"]
    for path in (
        "/api/v1/kb/workbench/master-visual-scan-aliases/status",
        "/api/v1/kb/workbench/master-visual-scan-aliases/catalog",
        "/api/v1/kb/workbench/master-visual-scan-aliases/{master_node_id}",
        "/api/v1/kb/workbench/master-visual-scan-aliases/{master_node_id}/question-crops/{crop_id}",
    ):
        assert path in paths
    schemas = contract["components"]["schemas"]
    for schema_name in (
        "MasterVisualScanAliasAuthority",
        "MasterVisualScanAliasCounts",
        "MasterVisualScanAliasIntegrity",
        "MasterVisualScanAliasCatalogItem",
        "MasterVisualScanAliasStatusEnvelope",
        "MasterVisualScanAliasCatalogEnvelope",
        "MasterVisualScanAliasTargetAtomicPart",
        "MasterVisualScanAliasDetail",
        "MasterVisualScanAliasDetailEnvelope",
        "MasterAtomicVisualScanCoverage",
        "MasterAtomicCombinedVisualScanCoverage",
    ):
        assert schemas[schema_name]["additionalProperties"] is False

    missing_refs: set[str] = set()

    def visit(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                if (
                    key == "$ref"
                    and isinstance(nested, str)
                    and nested.startswith("#/components/schemas/")
                    and nested.rsplit("/", 1)[1] not in schemas
                ):
                    missing_refs.add(nested)
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(contract)
    assert missing_refs == set()
    payloads = (
        ("MasterVisualScanAliasStatusEnvelope", reader.status()),
        ("MasterVisualScanAliasCatalogEnvelope", reader.catalog()),
        ("MasterVisualScanAliasDetailEnvelope", reader.detail(QUALITY_NOTE_ID)),
        ("MasterVisualScanAliasDetailEnvelope", reader.detail(ONE_TO_MANY_ID)),
    )
    for schema_name, data in payloads:
        envelope = {
            "contract_version": "shchem.gateway.v1",
            "request_id": "alias-schema-test",
            "data": data,
        }
        assert _schema_errors(contract, schema_name, envelope) == []


def test_http_alias_routes_and_unified_master_coverage_are_safe_and_zero_write(
    tmp_path,
):
    state_root = tmp_path / "state"
    config = build_config(state_root)
    contract = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    with running_server(config) as server:
        state_before = _tree_snapshot(state_root)
        product_before = _tree_snapshot(PRODUCT)

        status, status_payload, headers = _http_get(
            server, "/api/v1/kb/workbench/master-visual-scan-aliases/status"
        )
        assert status == 200
        assert headers.get("Cache-Control") == "no-store"
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert status_payload["data"]["counts"] == EXPECTED_COUNTS
        assert _schema_errors(
            contract, "MasterVisualScanAliasStatusEnvelope", status_payload
        ) == []

        status, catalog_payload, headers = _http_get(
            server, "/api/v1/kb/workbench/master-visual-scan-aliases/catalog"
        )
        assert status == 200
        assert headers.get("Cache-Control") == "no-store"
        catalog = catalog_payload["data"]
        assert catalog["count"] == 14
        assert catalog["new_physical_scans"] == 0
        serialized_catalog = json.dumps(catalog, ensure_ascii=False)
        assert "reference_answer_text" not in serialized_catalog
        assert "非官方答案整页标BD" not in serialized_catalog
        assert _schema_errors(
            contract, "MasterVisualScanAliasCatalogEnvelope", catalog_payload
        ) == []

        status, detail_payload, _ = _http_get(
            server,
            f"/api/v1/kb/workbench/master-visual-scan-aliases/{ONE_TO_MANY_ID}",
        )
        assert status == 200
        detail = detail_payload["data"]
        assert detail["relation_type"] == "alias_coverage_1_to_many"
        assert len(detail["target_atomic_parts"]) == 2
        assert detail["answer_projection"]["merged_reference_answer_text"] is None
        assert "scan_classification" not in detail
        assert _schema_errors(
            contract, "MasterVisualScanAliasDetailEnvelope", detail_payload
        ) == []

        crop_path = (
            f"/api/v1/kb/workbench/master-visual-scan-aliases/{QUALITY_NOTE_ID}"
            f"/question-crops/{QUALITY_NOTE_CROP_ID}"
        )
        status, raw, headers = _http_get(server, crop_path)
        assert status == 200
        assert raw.startswith(b"\x89PNG\r\n\x1a\n")
        assert headers.get("Cache-Control") == "no-store"
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert headers.get("Content-Disposition") is None

        status, payload, _ = _http_get(
            server,
            f"/api/v1/kb/workbench/master-visual-scan-aliases/{QUALITY_NOTE_ID}"
            "/question-crops/PT2026-T3-Q01-ANSWER-S1",
        )
        assert status == 403
        assert payload["error"]["code"] == "master_alias_crop_role_denied"

        status, payload, _ = _http_get(
            server, "/api/v1/kb/workbench/master-atomic/status"
        )
        assert status == 200
        assert payload["data"]["visual_scan_coverage"] == {
            "master_atomic_inventory": 470,
            "wave1_exact_visual_scanned": 169,
            "direct_master_visual_scanned": 214,
            "alias_existing_visual_scanned": 14,
            "visual_scanned_master_atomic": 397,
            "remaining_unscanned": 73,
            "direct_exact_overlap": 0,
            "alias_overlap_with_exact_or_direct": 0,
        }
        assert _schema_errors(
            contract, "MasterAtomicWorkbenchStatusEnvelope", payload
        ) == []

        status, payload, _ = _http_get(
            server,
            f"/api/v1/kb/workbench/master-atomic/{QUALITY_NOTE_ID}",
        )
        assert status == 200
        master_detail = payload["data"]
        assert master_detail["node"]["visual_scan_coverage"] == {
            "coverage_kind": "alias_existing_visual_scan",
            "detail_available": True,
            "new_physical_scans": 0,
            "target_atomic_count": 1,
        }
        assert master_detail["visual_scan_alias"]["master_node_id"] == QUALITY_NOTE_ID
        assert _schema_errors(
            contract, "MasterAtomicWorkbenchDetailEnvelope", payload
        ) == []

        status, direct_payload, _ = _http_get(
            server, "/api/v1/kb/workbench/master-direct-scans/catalog"
        )
        assert status == 200
        direct_data = direct_payload["data"]
        assert direct_data["count"] == len(direct_data["master_node_ids"]) == 214
        assert direct_data["coverage"]["direct_master_visual_scanned"] == (
            direct_data["count"]
        )
        assert direct_data["coverage"]["visual_scanned_master_atomic"] == 383
        assert len(direct_data["products"]) == 9

        status, payload, _ = _http_get(
            server,
            "/api/v1/kb/workbench/master-visual-scan-aliases/status?unexpected=1",
        )
        assert status == 400
        assert payload["error"]["code"] == "master_visual_scan_alias_query_unsupported"
        assert _tree_snapshot(state_root) == state_before
        assert _tree_snapshot(PRODUCT) == product_before


def test_alias_drift_blocks_only_alias_and_unified_visible_layer(
    tmp_path, monkeypatch
):
    with tempfile.TemporaryDirectory(prefix="alias-http-drift-", dir=SHCHEM_ROOT) as raw:
        copied = Path(raw) / "product"
        shutil.copytree(PRODUCT, copied)
        records = copied / "alias_records.jsonl"
        records.write_bytes(records.read_bytes() + b"\n")
        monkeypatch.setattr(
            alias_module, "PRODUCT_RELATIVE", copied.relative_to(SHCHEM_ROOT)
        )
        # The frozen Master470/exact169 reader remains independently usable.
        assert MasterWave1WorkbenchReader(SHCHEM_ROOT).status()["counts"][
            "exact_identity"
        ] == 169
        config = build_config(tmp_path / "state")
        with running_server(config) as server:
            status, payload, _ = _http_get(
                server, "/api/v1/kb/workbench/master-visual-scan-aliases/status"
            )
            assert status == 409
            assert payload["error"]["code"] == "master_alias_binding_mismatch"

            status, payload, _ = _http_get(
                server, "/api/v1/kb/workbench/master-atomic/status"
            )
            assert status == 409
            assert payload["error"]["code"] == "master_alias_binding_mismatch"

            status, payload, _ = _http_get(
                server, "/api/v1/kb/workbench/master-direct-scans/catalog"
            )
            assert status == 200
            assert payload["data"]["count"] == 214
            assert len(payload["data"]["products"]) == 9


def test_direct_bound_file_drift_after_success_blocks_every_unified_master_read(
    tmp_path, monkeypatch
):
    with tempfile.TemporaryDirectory(prefix="direct-live-drift-", dir=SHCHEM_ROOT) as raw:
        copied = Path(raw) / "research-product"
        shutil.copytree(SHCHEM_ROOT / direct_module.PRODUCT_RELATIVE, copied)
        monkeypatch.setattr(
            direct_module, "PRODUCT_RELATIVE", copied.relative_to(SHCHEM_ROOT)
        )
        config = build_config(tmp_path / "state")
        with running_server(config) as server:
            status, payload, _ = _http_get(
                server, "/api/v1/kb/workbench/master-atomic/status"
            )
            assert status == 200
            assert payload["data"]["visual_scan_coverage"][
                "visual_scanned_master_atomic"
            ] == 397

            records = copied / "scan_records.jsonl"
            records.write_bytes(records.read_bytes() + b"\n")
            for path in (
                "/api/v1/kb/workbench/master-atomic/status",
                "/api/v1/kb/workbench/master-atomic?limit=1&offset=0",
                f"/api/v1/kb/workbench/master-atomic/{QUALITY_NOTE_ID}",
            ):
                status, payload, _ = _http_get(server, path)
                assert status == 409
                assert payload["error"]["code"] == "master_direct_scan_binding_mismatch"
