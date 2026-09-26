from __future__ import annotations

import hashlib
import http.client
import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, RefResolver

from integrations.deeptutor_shchem_v1.caoyang2_h2_direct_visual_scan import (
    PRODUCT_RELATIVE as CAOYANG_PRODUCT_RELATIVE,
)
from integrations.deeptutor_shchem_v1.fengxian2025_theme2_direct_visual_scan import (
    PRODUCT_RELATIVE as FENGXIAN_PRODUCT_RELATIVE,
)
from integrations.deeptutor_shchem_v1.master_direct_visual_scan import (
    MasterDirectVisualScanError,
    MasterDirectVisualScanReader,
    PRODUCT_RELATIVE as RESEARCH_PRODUCT_RELATIVE,
)
from integrations.deeptutor_shchem_v1.master_wave1_workbench import (
    PRODUCT_RELATIVE as MASTER_PRODUCT_RELATIVE,
)
from integrations.deeptutor_shchem_v1.pudong2026_first_mock_theme_direct_visual_scan import (
    PRODUCT_RELATIVE as PUDONG_PRODUCT_RELATIVE,
)
from integrations.deeptutor_shchem_v1.jiading2025_theme1_direct_visual_scan import (
    PRODUCT_RELATIVE as JIADING_PRODUCT_RELATIVE,
)
from integrations.deeptutor_shchem_v1.huangpu2025_theme4_direct_visual_scan import (
    HONGKOU2026_SECOND_MOCK_THEME4_CONFIG,
    PRODUCT_RELATIVE as HUANGPU_PRODUCT_RELATIVE,
    QIBAO2025_OPENING_THEME4_CONFIG,
)
from integrations.deeptutor_shchem_v1.shanghai_exam_2026_recall_direct_visual_scan import (
    DIFFICULTY_CATEGORICAL_VALUES,
    DIFFICULTY_NUMERIC_DIMENSIONS,
    EXPECTED_CROP_CATEGORY_COUNTS,
    EXPECTED_MANIFEST_FILE_SHA256,
    EXPECTED_MANIFEST_SELF_SHA256,
    EXPECTED_MASTER_IDS,
    KNOWN_QUALITY_NOTES,
    PAPER_ID,
    PRODUCT_ID,
    PRODUCT_RELATIVE,
    FACTOR_IDS,
    ShanghaiExam2026RecallDirectVisualScanReader,
    _validate_difficulty_factor_values,
    _safe_relative,
)
from integrations.deeptutor_shchem_v1.shanghai_school_h2_direct_visual_scan import (
    PRODUCT_RELATIVE as SHS_PRODUCT_RELATIVE,
)
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    TOKEN_A,
    build_config,
    running_server,
)


WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
OPENAPI = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
)


def _browser_headers(server) -> dict[str, str]:
    return {
        "Origin": f"http://127.0.0.1:{server.server_address[1]}",
        "Sec-Fetch-Site": "same-origin",
        "Authorization": f"Bearer {TOKEN_A}",
    }


def _request(server, path: str):
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=60
    )
    connection.request("GET", path, headers=_browser_headers(server))
    response = connection.getresponse()
    raw = response.read()
    headers = response.headers
    status = response.status
    content_type = response.getheader("Content-Type") or ""
    connection.close()
    if "application/json" in content_type:
        return status, json.loads(raw.decode("utf-8")), headers
    return status, raw, headers


def _tree_snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _copy_product_with_sources(
    source_root: Path, target_root: Path, relative: Path
) -> None:
    source_product = source_root / relative
    target_product = target_root / relative
    target_product.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_product, target_product, dirs_exist_ok=True)
    manifest = json.loads(
        (source_product / "manifest.json").read_text(encoding="utf-8")
    )
    source_bindings = manifest.get("source_bindings")
    if source_bindings is None:
        source_manifest = json.loads(
            (source_product / "source_manifest.json").read_text(encoding="utf-8")
        )
        source_bindings = source_manifest.get("source_bindings")
        if source_bindings is None:
            source_bindings = source_manifest.get("sources")
        if source_bindings is None:
            source_bindings = source_manifest.get("files")
        if source_bindings is None:
            source_bindings = [
                binding
                for key in ("source_assets", "protected_inputs", "textbook_sources")
                for binding in source_manifest.get(key, ())
            ]
    assert isinstance(source_bindings, list) and source_bindings
    for binding in source_bindings:
        workspace_relative = binding["path"].startswith(("课本/", "sh-chem-db/"))
        source_base = source_root.parent if workspace_relative else source_root
        target_base = target_root.parent if workspace_relative else target_root
        source = source_base / binding["path"]
        target = target_base / binding["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _isolated_root(temp: Path) -> Path:
    root = temp / "sh-chem-db"
    for relative in (
        RESEARCH_PRODUCT_RELATIVE,
        SHS_PRODUCT_RELATIVE,
        CAOYANG_PRODUCT_RELATIVE,
        PRODUCT_RELATIVE,
        PUDONG_PRODUCT_RELATIVE,
        JIADING_PRODUCT_RELATIVE,
        HUANGPU_PRODUCT_RELATIVE,
        QIBAO2025_OPENING_THEME4_CONFIG.product_relative,
        HONGKOU2026_SECOND_MOCK_THEME4_CONFIG.product_relative,
        FENGXIAN_PRODUCT_RELATIVE,
        MASTER_PRODUCT_RELATIVE,
    ):
        _copy_product_with_sources(SHCHEM_ROOT, root, relative)
    return root


def _schema_validator(contract: dict, name: str) -> Draft202012Validator:
    return Draft202012Validator(
        contract["components"]["schemas"][name],
        resolver=RefResolver.from_schema(contract),
    )


def test_recall35_reader_projects_exact_source_answers_and_safe_catalog(monkeypatch):
    reader = ShanghaiExam2026RecallDirectVisualScanReader(SHCHEM_ROOT)
    snapshot = reader._snapshot()
    monkeypatch.setattr(reader, "_snapshot", lambda: snapshot)
    status = reader.status()
    catalog = reader.catalog()

    assert status["product_id"] == PRODUCT_ID
    assert status["paper_id"] == PAPER_ID
    assert status["coverage"] == {
        "master_atomic_inventory": 470,
        "wave1_exact_visual_scanned": 169,
        "direct_master_visual_scanned": 35,
        "visual_scanned_master_atomic": 204,
        "remaining_unscanned": 266,
        "direct_exact_overlap": 0,
    }
    assert status["integrity"]["manifest_self_sha256"] == (
        EXPECTED_MANIFEST_SELF_SHA256
    )
    assert status["integrity"]["manifest_file_sha256"] == (
        EXPECTED_MANIFEST_FILE_SHA256
    )
    assert catalog["count"] == len(catalog["items"]) == 35
    assert set(catalog["master_node_ids"]) == EXPECTED_MASTER_IDS
    assert sum(item["has_quality_note"] for item in catalog["items"]) == 5
    assert all(
        item["availability"] == "present_part_aligned"
        and item["source_authority"] == "nonofficial_reference"
        and "reference_answer_text" not in item
        for item in catalog["items"]
    )
    assert catalog["paper_identity_boundary"][
        "coverage_scope"
    ] == "master_structured_themes_1_2_3_5_only_35_atomic"
    assert catalog["paper_identity_boundary"][
        "complete_five_theme_claim_allowed"
    ] is False
    assert catalog["paper_identity_boundary"]["official_status"] == "nonofficial"
    assert catalog["paper_identity_boundary"]["source_accounts"] == [
        "申教在线",
        "靠谱提分",
    ]
    assert catalog["paper_identity_boundary"]["source_b_exact_pages_local"] is False

    for record in snapshot.records:
        node_id = record["hierarchy"]["atomic_part_id"]
        detail = reader.detail(node_id)
        assert detail["reference_answer"] == {
            "availability": "present_part_aligned",
            "reference_answer_text": record["answer"]["reference_summary_zh"],
            "source_authority": "nonofficial_reference",
            "independently_verified": False,
            "quality_note": record["answer"]["quality_note"],
        }
        assert detail["answer_boundary"] == {
            "availability": "present_part_aligned",
            "authority": "nonofficial_reference",
            "verified": False,
        }
        serialized = json.dumps(detail, ensure_ascii=False, sort_keys=True)
        for forbidden in (
            "reference_summary_zh",
            "visual_alignment_evidence",
            "answer_crop",
            "answer_page",
            "crop_path",
            "output_path",
            "source_path",
            "source_bindings",
            "outputs",
            "rule_bindings",
            "http://",
            "https://",
        ):
            assert forbidden not in serialized


def test_recall35_question_and_shared_pngs_are_safe_and_all_answer_crops_are_403(
    monkeypatch,
):
    reader = ShanghaiExam2026RecallDirectVisualScanReader(SHCHEM_ROOT)
    snapshot = reader._snapshot()
    monkeypatch.setattr(reader, "_snapshot", lambda: snapshot)
    record = snapshot.records[0]
    node_id = record["hierarchy"]["atomic_part_id"]
    detail = reader.detail(node_id)
    roles = {item["evidence_role"] for item in detail["evidence_descriptors"]}
    assert roles == {"question", "shared_material"}
    for descriptor in detail["evidence_descriptors"]:
        payload = reader.question_crop(node_id, descriptor["crop_id"])
        assert payload.data.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(payload.data) == descriptor["bytes"] <= 1048576
        assert hashlib.sha256(payload.data).hexdigest() == descriptor["sha256"]

    assert len(snapshot.answer_crop_ids) == EXPECTED_CROP_CATEGORY_COUNTS[
        "nonofficial_reference_answer"
    ]
    for answer_crop_id in snapshot.answer_crop_ids:
        with pytest.raises(MasterDirectVisualScanError) as exc_info:
            reader.question_crop(node_id, answer_crop_id)
        assert exc_info.value.status == 403
        assert exc_info.value.code == "master_direct_scan_crop_role_denied"


def test_recall35_quality_notes_are_exactly_five_and_nonblocking(monkeypatch):
    reader = ShanghaiExam2026RecallDirectVisualScanReader(SHCHEM_ROOT)
    snapshot = reader._snapshot()
    monkeypatch.setattr(reader, "_snapshot", lambda: snapshot)
    records = {
        record["hierarchy"]["atomic_part_id"]: record
        for record in snapshot.records
    }
    assert set(KNOWN_QUALITY_NOTES) == {
        "Q11",
        "LE2026-S3-Q17-P1",
        "LE2026-S3-Q23-P1",
        "LE2026-S5-Q26-P1",
        "LE2026-S5-Q30-P1",
    }
    for node_id, code in KNOWN_QUALITY_NOTES.items():
        record = records[node_id]
        assert record["candidate_analysis"]["known_quality_note_code"] == code
        assert record["answer"]["quality_note"]
        detail = reader.detail(node_id)
        assert detail["reference_answer"]["reference_answer_text"] == record[
            "answer"
        ]["reference_summary_zh"]
        assert detail["reference_answer"]["quality_note"] == record["answer"][
            "quality_note"
        ]
        assert detail["candidate_analysis"]["correctness_verified"] is False
        assert detail["authority"]["answer_verified"] is False
        assert detail["authority"]["teaching_use_allowed"] is False


def test_registered_product_aggregate_is_exactly_224_393_77_and_pairwise_disjoint():
    reader = MasterDirectVisualScanReader(SHCHEM_ROOT)
    status = reader.status()
    catalog = reader.catalog()
    assert status["counts"] == {
        "direct_scan_products": 10,
        "direct_scan_records": 224,
        "visual_scan_completed": 224,
        "blocked_pending_broader_crop": 0,
    }
    assert status["coverage"] == {
        "master_atomic_inventory": 470,
        "wave1_exact_visual_scanned": 169,
        "direct_master_visual_scanned": 224,
        "visual_scanned_master_atomic": 393,
        "remaining_unscanned": 77,
        "direct_exact_overlap": 0,
    }
    assert status["integrity"]["batch_count"] == len(status["products"]) == 10
    assert status["integrity"]["record_count"] == 224
    assert status["integrity"]["direct_batch_disjoint_verified_on_read"] is True
    assert [product["count"] for product in status["products"]] == [
        47,
        43,
        38,
        35,
        11,
        10,
        11,
            10,
            9,
            10,
        ]
    assert catalog["count"] == len(catalog["items"]) == 224
    assert len(set(catalog["master_node_ids"])) == 224
    assert sum(item["product_id"] == PRODUCT_ID for item in catalog["items"]) == 35
    assert "reference_answer_text" not in json.dumps(
        catalog, ensure_ascii=False, sort_keys=True
    )


def test_http_actual_dtos_validate_openapi_and_gets_do_not_write_or_log_answers(
    tmp_path: Path,
):
    product_root = SHCHEM_ROOT / PRODUCT_RELATIVE
    before = _tree_snapshot(product_root)
    reader = ShanghaiExam2026RecallDirectVisualScanReader(SHCHEM_ROOT)
    snapshot = reader._snapshot()
    record = max(
        snapshot.records, key=lambda item: len(item["answer"]["reference_summary_zh"])
    )
    node_id = record["hierarchy"]["atomic_part_id"]
    answer_text = record["answer"]["reference_summary_zh"]
    question_crop_id = next(
        item["crop_id"]
        for item in record["viewed_evidence"]
        if item["evidence_role"] == "question"
    )
    answer_crop_id = record["answer"]["visual_alignment_evidence"][0]["crop_id"]
    contract = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    config = build_config(tmp_path)
    with running_server(config) as server:
        status_code, status_body, status_headers = _request(
            server, "/api/v1/kb/workbench/master-direct-scans/status"
        )
        catalog_code, catalog_body, catalog_headers = _request(
            server, "/api/v1/kb/workbench/master-direct-scans/catalog"
        )
        detail_code, detail_body, detail_headers = _request(
            server, f"/api/v1/kb/workbench/master-direct-scans/{node_id}"
        )
        crop_code, crop_body, crop_headers = _request(
            server,
            f"/api/v1/kb/workbench/master-direct-scans/{node_id}/question-crops/{question_crop_id}",
        )
        answer_code, answer_body, answer_headers = _request(
            server,
            f"/api/v1/kb/workbench/master-direct-scans/{node_id}/question-crops/{answer_crop_id}",
        )

    assert (status_code, catalog_code, detail_code, crop_code, answer_code) == (
        200,
        200,
        200,
        200,
        403,
    )
    _schema_validator(contract, "MasterDirectScanStatusEnvelope").validate(
        status_body
    )
    _schema_validator(contract, "MasterDirectScanCatalogEnvelope").validate(
        catalog_body
    )
    _schema_validator(contract, "MasterDirectScanDetailEnvelope").validate(
        detail_body
    )
    assert detail_body["data"]["reference_answer"]["reference_answer_text"] == (
        answer_text
    )
    assert answer_text not in json.dumps(catalog_body, ensure_ascii=False)
    assert crop_body.startswith(b"\x89PNG\r\n\x1a\n")
    assert answer_body["error"]["code"] == "master_direct_scan_crop_role_denied"
    for headers in (
        status_headers,
        catalog_headers,
        detail_headers,
        crop_headers,
        answer_headers,
    ):
        assert headers.get("Cache-Control") == "no-store"
        assert headers.get("X-Content-Type-Options") == "nosniff"
    audit_path = tmp_path / "audit/gateway.jsonl"
    if audit_path.is_file():
        assert answer_text not in audit_path.read_text(encoding="utf-8")
    assert _tree_snapshot(product_root) == before


def test_any_new_product_drift_fails_new_reader_and_whole_aggregate(tmp_path: Path):
    root = _isolated_root(tmp_path)
    reader = ShanghaiExam2026RecallDirectVisualScanReader(root)
    assert reader.catalog()["count"] == 35
    assert MasterDirectVisualScanReader(root).catalog()["count"] == 224

    readme = root / PRODUCT_RELATIVE / "README.md"
    readme.write_bytes(readme.read_bytes() + b"\n")
    with pytest.raises(MasterDirectVisualScanError):
        ShanghaiExam2026RecallDirectVisualScanReader(root).status()
    with pytest.raises(MasterDirectVisualScanError):
        MasterDirectVisualScanReader(root).status()


@pytest.mark.parametrize(
    "value",
    [
        "../escape.json",
        "nested/../escape.json",
        "/absolute.json",
        "C:/absolute.json",
        "nested\\windows.json",
        "./relative.json",
    ],
)
def test_recall_reader_rejects_unsafe_paths_component_by_component(value: str):
    with pytest.raises(MasterDirectVisualScanError):
        _safe_relative(value)


def test_recall_png_crc_validator_rejects_single_byte_crc_drift():
    reader = ShanghaiExam2026RecallDirectVisualScanReader(SHCHEM_ROOT)
    snapshot = reader._snapshot()
    crop = next(
        value
        for value in snapshot.crop_by_id.values()
        if value["category"] == "question"
    )
    raw = snapshot.output_bytes[crop["relative_output"]]
    assert reader._png_dimensions(raw) == (crop["width"], crop["height"])
    mutated = bytearray(raw)
    mutated[-5] ^= 1
    assert reader._png_dimensions(bytes(mutated)) is None


def test_difficulty_factor_values_fail_closed_against_bound_vocabulary():
    valid = [
        {
            "dimension_id": dimension_id,
            "value": (
                1
                if dimension_id in DIFFICULTY_NUMERIC_DIMENSIONS
                else DIFFICULTY_CATEGORICAL_VALUES[dimension_id][0]
            ),
        }
        for dimension_id in FACTOR_IDS
    ]
    _validate_difficulty_factor_values(valid)

    mutations = {
        "information_transformations": "1",
        "reasoning_chain_steps": -1,
        "knowledge_module_span": True,
        "representation_switches": 1.5,
        "calculation_load": "moderate",
        "experiment_load": "light",
        "openness": "constrained_open",
        "unfamiliarity": "novel",
        "language_load": "moderate",
        "dependency_on_prior_parts": "shared_theme_context",
    }
    for dimension_id, illegal_value in mutations.items():
        candidate = deepcopy(valid)
        next(
            factor
            for factor in candidate
            if factor["dimension_id"] == dimension_id
        )["value"] = illegal_value
        with pytest.raises(MasterDirectVisualScanError) as exc_info:
            _validate_difficulty_factor_values(candidate)
        assert exc_info.value.code == (
            "master_direct_scan_difficulty_vocabulary_invalid"
        )
