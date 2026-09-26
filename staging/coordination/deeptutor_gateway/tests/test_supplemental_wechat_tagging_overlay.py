from __future__ import annotations

from collections import Counter
import copy
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

import integrations.deeptutor_shchem_v1.supplemental_wechat_tagging_overlay as overlay_module
from integrations.deeptutor_shchem_v1.service import GatewayService
from integrations.deeptutor_shchem_v1.supplemental_visual_scan import (
    SupplementalVisualScanReader,
)
from integrations.deeptutor_shchem_v1.supplemental_wechat_tagging_overlay import (
    BASE_REGISTRY_ID,
    DATA_SNAPSHOT_ID,
    PRODUCT_ID,
    REGISTRY_ID,
    REGISTRY_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SCOPE,
    SupplementalWechatTaggingOverlayError,
    SupplementalWechatTaggingOverlayReader,
)
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    build_config,
    request,
    running_server,
)


WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
PRODUCT = (
    SHCHEM_ROOT
    / "kb/classification/"
    "supplemental_wechat_textbook_tagging_v1_2026-08-27"
)
OPENAPI = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
)


@pytest.fixture(scope="module")
def live_gateway_service(tmp_path_factory):
    config = build_config(tmp_path_factory.mktemp("supp-tagging-overlay-service"))
    return GatewayService(config), config.principals[0]


def _record_ids() -> list[str]:
    return [
        json.loads(line)["hierarchy"]["atomic_part_id"]
        for line in (PRODUCT / "tagging_records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]


def _walk(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key, nested
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def test_real_overlay_status_is_closed_to_57_exam_atomics_and_keeps_gates_false():
    reader = SupplementalWechatTaggingOverlayReader(SHCHEM_ROOT)
    status = reader.status()

    assert status["schema_version"] == SCHEMA_VERSION
    assert status["scope"] == SCOPE
    assert status["data_snapshot_id"] == DATA_SNAPSHOT_ID
    assert status["registry"] == {
        "registry_id": REGISTRY_ID,
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "base_registry_id": BASE_REGISTRY_ID,
        "product_id": PRODUCT_ID,
    }
    assert status["counts"] == {
        "papers": 3,
        "theme_big_questions": 6,
        "printed_questions": 51,
        "atomic_parts": 57,
        "base_supplemental_atomic_parts": 86,
        "base_shanghai_exam_atomic_parts": 57,
        "base_external_handout_atomic_parts": 29,
        "overlay_external_handout_atomic_parts": 0,
        "textbook_mapping_entries": 117,
        "complete_textbook_mappings": 53,
        "partial_blocked_textbook_mappings": 4,
        "difficulty_factor_entries": 570,
        "answer_aligned_nonofficial_unverified": 57,
    }
    assert status["authority"]["candidate_only"] is True
    assert status["authority"]["read_only"] is True
    assert all(
        value is False
        for key, value in status["authority"].items()
        if key not in {"candidate_only", "read_only"}
    )
    assert all(status["integrity"].values())
    assert reader._snapshot() is reader._snapshot()


def test_all_real_details_have_fixed_safe_shape_and_match_base_classification():
    base = SupplementalVisualScanReader(SHCHEM_ROOT)
    reader = SupplementalWechatTaggingOverlayReader(
        SHCHEM_ROOT, base_reader=base
    )
    record_ids = _record_ids()
    base_items = {
        item["node_id"]: item
        for item in base.list_atomic(limit=200, offset=0)["items"]
    }
    assert len(record_ids) == len(set(record_ids)) == 57

    expected_keys = {
        "schema_version",
        "scope",
        "data_snapshot_id",
        "node_id",
        "hierarchy",
        "classification",
        "difficulty",
        "source_identity",
        "source_layer",
        "textbook_directory_mapping",
        "answer_alignment_boundary",
        "authority",
    }
    for node_id in record_ids:
        detail = reader.detail(node_id)
        assert set(detail) == expected_keys
        assert detail["node_id"] == node_id
        assert detail["classification"] == base.detail(node_id)["visual_scan"][
            "scan_classification"
        ]
        assert set(detail["classification"]) == {
            "A",
            "C",
            "R",
            "RP",
            "item_type",
            "primary_K",
            "selection_rule",
            "supporting_K",
        }
        assert set(detail["difficulty"]) == {
            "basis_zh",
            "cognitive_prelabel",
            "factors",
            "is_measured",
            "measured_difficulty",
        }
        assert len(detail["difficulty"]["factors"]) == 10
        assert detail["source_layer"]["source_kind"] == (
            "shanghai_exam_wechat_archive"
        )
        assert detail["source_layer"]["official_status"] == "nonofficial"
        assert detail["source_identity"]["paper_face"] != (
            detail["source_identity"]["wechat_article"]
        )
        assert detail["answer_alignment_boundary"]["availability"] == (
            "present_part_aligned"
        )
        assert detail["answer_alignment_boundary"]["authority"] == (
            "nonofficial_reference"
        )
        assert detail["answer_alignment_boundary"][
            "independently_verified"
        ] is False
        assert detail["answer_alignment_boundary"][
            "reference_answer_exposed"
        ] is False
        assert base_items[node_id]["source_kind"] == (
            "shanghai_exam_wechat_archive"
        )
        for key, value in _walk(detail):
            assert key not in {
                "path",
                "source_path",
                "source_root",
                "sha256",
                "reference_summary_zh",
                "reference_answer_text",
                "visual_evidence",
            }
            assert not key.endswith("_sha256")
            if isinstance(value, str):
                assert "http://" not in value
                assert "https://" not in value
                assert "sh-chem-db/" not in value
                assert "kb/classification/" not in value


def test_four_chapter_level_blockers_remain_null_and_unfabricated():
    reader = SupplementalWechatTaggingOverlayReader(SHCHEM_ROOT)
    expected = {
        "SJ2026-EM-S2-Q5-P1": ("K10", "TB-E1-C3"),
        "SJ2026-EM-S3-Q9-P1": ("K17", "TB-E3-C3"),
        "MVPPLUS-A-PL-0bb39aa129f46783-T1-Q9-P1": (
            "K14",
            "TB-E2-C3",
        ),
        "HK2026-EM-S2-Q9-P1": ("K09", "TB-E1-C2"),
    }
    for node_id, (knowledge_tag, chapter_id) in expected.items():
        mapping = reader.detail(node_id)["textbook_directory_mapping"]
        assert mapping["mapping_status"] == "partial_blocked"
        blocked = [
            entry
            for entry in mapping["entries"]
            if entry["mapping_status"] == "blocked_pending_review"
        ]
        assert len(blocked) == 1
        entry = blocked[0]
        assert entry["knowledge_tag"] == knowledge_tag
        assert entry["chapter_id"] == chapter_id
        assert entry["section_id"] is None
        assert entry["section_number"] is None
        assert entry["section_title"] is None
        assert entry["blocker_or_note"]


def test_handout_and_unknown_ids_are_not_exposed_by_the_overlay():
    base = SupplementalVisualScanReader(SHCHEM_ROOT)
    handout_id = next(
        item["node_id"]
        for item in base.list_atomic(limit=200, offset=0)["items"]
        if item["source_kind"] == "external_teaching_handout"
    )
    reader = SupplementalWechatTaggingOverlayReader(
        SHCHEM_ROOT, base_reader=base
    )
    for node_id in (handout_id, "UNKNOWN-SUPPLEMENTAL-ATOMIC"):
        with pytest.raises(SupplementalWechatTaggingOverlayError) as error:
            reader.detail(node_id)
        assert error.value.status == 404
        assert error.value.code == "supplemental_tagging_not_found"


def test_status_and_detail_are_deep_copies_of_one_cached_snapshot():
    reader = SupplementalWechatTaggingOverlayReader(SHCHEM_ROOT)
    status = reader.status()
    status["counts"]["atomic_parts"] = 0
    assert reader.status()["counts"]["atomic_parts"] == 57

    node_id = "SJ2026-EM-S1-Q1-P1"
    detail = reader.detail(node_id)
    detail["classification"]["A"].append("FABRICATED")
    detail["textbook_directory_mapping"]["entries"][0][
        "section_number"
    ] = "9.9"
    fresh = reader.detail(node_id)
    assert "FABRICATED" not in fresh["classification"]["A"]
    assert fresh["textbook_directory_mapping"]["entries"][0][
        "section_number"
    ] != "9.9"


def test_registry_file_hash_tamper_fails_closed(monkeypatch):
    monkeypatch.setattr(overlay_module, "REGISTRY_FILE_SHA256", "0" * 64)
    reader = SupplementalWechatTaggingOverlayReader(SHCHEM_ROOT)
    with pytest.raises(SupplementalWechatTaggingOverlayError) as error:
        reader.status()
    assert error.value.code == "supplemental_tagging_drift"


def test_registry_self_hash_tamper_fails_closed(monkeypatch):
    original = SupplementalWechatTaggingOverlayReader._read_bound_file

    def tampered_read(self, relative, expected_sha256, label, *, root=None):
        raw = original(
            self, relative, expected_sha256, label, root=root
        )
        if Path(relative) == overlay_module.REGISTRY_RELATIVE:
            value = json.loads(raw.decode("utf-8"))
            value["claims"]["official"] = True
            return json.dumps(value, ensure_ascii=False).encode("utf-8")
        return raw

    monkeypatch.setattr(
        SupplementalWechatTaggingOverlayReader,
        "_read_bound_file",
        tampered_read,
    )
    reader = SupplementalWechatTaggingOverlayReader(SHCHEM_ROOT)
    with pytest.raises(SupplementalWechatTaggingOverlayError) as error:
        reader.status()
    assert error.value.code in {
        "supplemental_tagging_registry_invalid",
        "supplemental_tagging_registry_drift",
    }


class _CountTamperedBase:
    def __init__(self, delegate: SupplementalVisualScanReader):
        self.delegate = delegate

    def status(self):
        value = copy.deepcopy(self.delegate.status())
        value["counts"]["external_handout_atomic_parts"] = 28
        return value

    def list_atomic(self, *, limit: int, offset: int):
        return self.delegate.list_atomic(limit=limit, offset=offset)

    def detail(self, node_id: str):
        return self.delegate.detail(node_id)


def test_base_public_cross_validation_failure_fails_closed():
    base = _CountTamperedBase(SupplementalVisualScanReader(SHCHEM_ROOT))
    reader = SupplementalWechatTaggingOverlayReader(
        SHCHEM_ROOT, base_reader=base
    )
    with pytest.raises(SupplementalWechatTaggingOverlayError) as error:
        reader.status()
    assert error.value.code == "supplemental_tagging_base_count_invalid"


def test_service_merges_overlay_into_all_57_exam_details_only(
    live_gateway_service,
):
    service, principal = live_gateway_service
    status = service.supplemental_visual_scan_status(principal)
    page = service.supplemental_visual_scan_list(
        principal, limit=200, offset=0
    )
    assert status["counts"]["atomic_parts"] == 86
    assert status["counts"]["shanghai_exam_atomic_parts"] == 57
    assert status["counts"]["external_handout_atomic_parts"] == 29
    assert page["count"] == page["total"] == len(page["items"]) == 86
    assert Counter(item["source_kind"] for item in page["items"]) == Counter(
        {
            "shanghai_exam_wechat_archive": 57,
            "external_teaching_handout": 29,
        }
    )
    assert all("tagging_overlay" not in item for item in page["items"])
    assert all("source_layer" not in item for item in page["items"])

    exam_details = {}
    for item in page["items"]:
        if item["source_kind"] != "shanghai_exam_wechat_archive":
            continue
        node_id = item["node_id"]
        detail = service.supplemental_visual_scan_detail(principal, node_id)
        node = detail["node"]
        visual = detail["visual_scan"]
        overlay = node["tagging_overlay"]
        exam_details[node_id] = detail

        assert node["node_id"] == overlay["node_id"] == node_id
        assert node["source_kind"] == "shanghai_exam_wechat_archive"
        assert node["source_layer"] == overlay["source_layer"][
            "evidence_level"
        ]
        assert overlay["classification"] == visual["scan_classification"]
        assert overlay["difficulty"] == visual["cognitive_difficulty"]
        assert overlay["hierarchy"]["paper_id"] == visual[
            "scan_hierarchy"
        ]["paper_id"]
        assert overlay["hierarchy"]["theme_big_question_id"] == visual[
            "scan_hierarchy"
        ]["theme_id"]
        assert overlay["hierarchy"]["printed_question_id"] == visual[
            "scan_hierarchy"
        ]["printed_question_id"]
        assert overlay["hierarchy"]["atomic_part_id"] == visual[
            "scan_hierarchy"
        ]["atomic_part_id"]
        assert overlay["hierarchy"]["theme_sequence"] == visual[
            "scan_hierarchy"
        ]["theme_sequence"]
        assert overlay["hierarchy"]["printed_sequence"] == visual[
            "scan_hierarchy"
        ]["printed_sequence"]
        assert overlay["hierarchy"]["atomic_sequence_in_printed"] == visual[
            "scan_hierarchy"
        ]["atomic_sequence_in_printed"]
        answer = overlay["answer_alignment_boundary"]
        assert answer["availability"] == visual["answer_boundary"][
            "availability"
        ]
        assert answer["authority"] == visual["answer_boundary"]["authority"]
        assert answer["source_authority"] == visual["reference_answer"][
            "source_authority"
        ]
        assert answer["independently_verified"] is False
        assert answer["answer_verified"] is False
        assert answer["reference_answer_exposed"] is False
        assert answer["answer_pixels_exposed"] is False

    assert len(exam_details) == 57
    assert Counter(
        detail["node"]["tagging_overlay"]["textbook_directory_mapping"][
            "mapping_status"
        ]
        for detail in exam_details.values()
    ) == Counter(
        {
            "complete_directory_level_unit_unknown": 53,
            "partial_blocked": 4,
        }
    )

    handout_item = next(
        item
        for item in page["items"]
        if item["source_kind"] == "external_teaching_handout"
    )
    handout = service.supplemental_visual_scan_detail(
        principal, handout_item["node_id"]
    )
    assert "tagging_overlay" not in handout["node"]
    assert "source_layer" not in handout["node"]

    expected_blocked = {
        "SJ2026-EM-S2-Q5-P1": ("K10", "TB-E1-C3"),
        "SJ2026-EM-S3-Q9-P1": ("K17", "TB-E3-C3"),
        "MVPPLUS-A-PL-0bb39aa129f46783-T1-Q9-P1": (
            "K14",
            "TB-E2-C3",
        ),
        "HK2026-EM-S2-Q9-P1": ("K09", "TB-E1-C2"),
    }
    for node_id, expected in expected_blocked.items():
        entries = exam_details[node_id]["node"]["tagging_overlay"][
            "textbook_directory_mapping"
        ]["entries"]
        blocked = [
            entry
            for entry in entries
            if entry["mapping_status"] == "blocked_pending_review"
        ]
        assert len(blocked) == 1
        entry = blocked[0]
        assert (entry["knowledge_tag"], entry["chapter_id"]) == expected
        assert entry["section_id"] is None
        assert entry["section_number"] is None
        assert entry["section_title"] is None


def test_http_detail_exposes_overlay_for_exam_but_not_handout(tmp_path):
    with running_server(build_config(tmp_path / "gateway")) as server:
        same_origin_headers = {
            "Origin": f"http://127.0.0.1:{server.server_address[1]}",
            "Sec-Fetch-Site": "same-origin",
        }
        list_code, list_envelope, _ = request(
            server,
            "GET",
            "/api/v1/kb/workbench/supplemental-scans?limit=200&offset=0",
            headers=same_origin_headers,
        )
        assert list_code == 200
        items = list_envelope["data"]["items"]
        assert list_envelope["data"]["total"] == 86
        exam_id = next(
            item["node_id"]
            for item in items
            if item["source_kind"] == "shanghai_exam_wechat_archive"
        )
        handout_id = next(
            item["node_id"]
            for item in items
            if item["source_kind"] == "external_teaching_handout"
        )

        exam_code, exam_envelope, _ = request(
            server,
            "GET",
            f"/api/v1/kb/workbench/supplemental-scans/{exam_id}",
            headers=same_origin_headers,
        )
        handout_code, handout_envelope, _ = request(
            server,
            "GET",
            f"/api/v1/kb/workbench/supplemental-scans/{handout_id}",
            headers=same_origin_headers,
        )
        assert exam_code == handout_code == 200
        exam_node = exam_envelope["data"]["node"]
        handout_node = handout_envelope["data"]["node"]
        assert exam_node["tagging_overlay"]["node_id"] == exam_id
        assert exam_node["source_layer"] == (
            "L2_PAGE_VERIFIED_SHANGHAI_EXAM"
        )
        assert "tagging_overlay" not in handout_node
        assert "source_layer" not in handout_node


def test_openapi_declares_optional_overlay_and_validates_all_service_details(
    live_gateway_service,
):
    service, principal = live_gateway_service
    contract = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    components = contract["components"]["schemas"]
    assert "SupplementalWechatTaggingOverlay" in components
    list_item_schema = components["SupplementalScanListItem"]
    assert "tagging_overlay" not in list_item_schema["required"]
    assert list_item_schema["properties"]["tagging_overlay"] == {
        "$ref": "#/components/schemas/SupplementalWechatTaggingOverlay"
    }
    assert "source_layer" not in list_item_schema["required"]
    assert list_item_schema["properties"]["source_layer"] == {
        "type": "string",
        "const": "L2_PAGE_VERIFIED_SHANGHAI_EXAM",
    }

    validator = Draft202012Validator(
        {
            "$ref": "#/components/schemas/SupplementalScanDetailData",
            "components": contract["components"],
        }
    )
    page = service.supplemental_visual_scan_list(
        principal, limit=200, offset=0
    )
    for item in page["items"]:
        detail = service.supplemental_visual_scan_detail(
            principal, item["node_id"]
        )
        errors = list(validator.iter_errors(detail))
        assert errors == [], [
            {
                "node_id": item["node_id"],
                "path": list(error.absolute_path),
                "message": error.message,
            }
            for error in errors[:10]
        ]
