from __future__ import annotations

from collections import Counter
import copy
import hashlib
import http.client
import json
import tempfile
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from integrations.deeptutor_shchem_v1.supplemental_visual_scan import (
    REGISTRY_FILE_SHA256,
    REGISTRY_ID,
    REGISTRY_SCHEMA_SHA256,
    REGISTRY_SCHEMA_VERSION,
    SCOPE,
    SupplementalVisualScanError,
    SupplementalVisualScanReader,
)
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    TOKEN_A,
    build_config,
    running_server,
)


WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
OPENAPI = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
)
REGISTRY_DIR = (
    SHCHEM_ROOT
    / "kb/classification/supplemental_visual_scan_registry_v1_2026-08-26"
)
REGISTRY = REGISTRY_DIR / "registry.json"
REGISTRY_SCHEMA = REGISTRY_DIR / "registry.schema.json"


def _request(server, path: str):
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=30
    )
    headers = {
        "Authorization": f"Bearer {TOKEN_A}",
        "Origin": f"http://127.0.0.1:{server.server_address[1]}",
        "Sec-Fetch-Site": "same-origin",
    }
    connection.request("GET", path, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    content_type = response.getheader("Content-Type") or ""
    headers_out = response.headers
    status = response.status
    connection.close()
    if "application/json" in content_type:
        return status, json.loads(raw.decode("utf-8")), headers_out
    return status, raw, headers_out


def test_supplemental_reader_matches_the_frozen_registry_snapshot_and_boundaries():
    reader = SupplementalVisualScanReader(SHCHEM_ROOT)
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    status = reader.status()
    assert status["scope"] == SCOPE
    assert status["data_snapshot_id"] == REGISTRY_FILE_SHA256
    assert status["registry"] == {
        "registry_id": REGISTRY_ID,
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "file_sha256": REGISTRY_FILE_SHA256,
        "self_sha256": "d2aaee8420c1d8681ff95a3a35965e1de70c07b056fc2e2c481ea9960feb4b35",
        "product_count": 10,
        "dynamic_counts": True,
    }
    assert status["counts"] == {
        "papers": 5,
        "theme_big_questions": 10,
        "printed_questions": 74,
        "atomic_parts": 86,
        "shanghai_exam_atomic_parts": 57,
        "external_handout_atomic_parts": 29,
        "question_pixels_available": 57,
        "answer_aligned": 86,
        "answer_unaligned": 0,
        "answer_absent": 0,
        "quality_notes": 25,
    }
    assert status["non_additivity"] == {
        "must_not_be_added_to_master470": True,
        "must_not_be_added_to_wave252": True,
        "handout_is_not_shanghai_exam": True,
    }
    assert [
        (product["product_id"], product["count"], product["source_kind"])
        for product in status["products"]
    ] == [
        (
            product["product_id"],
            product["record_count"],
            product["source_kind"],
        )
        for product in registry["products"]
    ]
    counts = status["counts"]
    assert counts["atomic_parts"] == (
        counts["shanghai_exam_atomic_parts"]
        + counts["external_handout_atomic_parts"]
    )
    assert counts["answer_aligned"] + counts["answer_unaligned"] + counts[
        "answer_absent"
    ] == counts["atomic_parts"]
    assert counts["atomic_parts"] == sum(
        product["count"] for product in status["products"]
    )


def test_registry_hashes_and_self_hash_are_pinned_without_copying_products_into_ui():
    raw = REGISTRY.read_bytes()
    registry = json.loads(raw.decode("utf-8"))
    schema_raw = REGISTRY_SCHEMA.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == REGISTRY_FILE_SHA256
    assert hashlib.sha256(schema_raw).hexdigest() == REGISTRY_SCHEMA_SHA256
    assert registry["registry_id"] == REGISTRY_ID
    assert registry["schema_version"] == REGISTRY_SCHEMA_VERSION
    assert registry["product_count"] == len(registry["products"]) == 10
    canonical = dict(registry)
    canonical["self_sha256"] = None
    canonical_raw = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert hashlib.sha256(canonical_raw).hexdigest() == registry["self_sha256"]
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    for product in registry["products"]:
        assert product["manifest_file_sha256"] not in app


def test_supplemental_list_has_exact_four_level_chains_and_controlled_labels():
    reader = SupplementalVisualScanReader(SHCHEM_ROOT)
    status = reader.status()
    expected_total = status["counts"]["atomic_parts"]
    page = reader.list_atomic(limit=200, offset=0)
    assert page["data_snapshot_id"] == status["data_snapshot_id"]
    assert page["total"] == page["count"] == expected_total
    assert len({item["node_id"] for item in page["items"]}) == expected_total
    assert all(
        [node["node_type"] for node in item["parent_chain"]]
        == ["paper", "theme_big_question", "printed_question", "atomic_part"]
        for item in page["items"]
    )
    assert all(item["authority"]["official"] is False for item in page["items"])
    assert all(
        item["difficulty"]["measured_difficulty"]
        == "blocked_pending_student_data"
        for item in page["items"]
    )
    handout = [
        item for item in page["items"] if item["source_kind"] == "external_teaching_handout"
    ]
    assert len(handout) == status["counts"]["external_handout_atomic_parts"]
    assert all(item["crop_refs"]["count"] == 0 for item in handout)
    assert sum(
        item["crop_refs"]["count"] > 0 for item in page["items"]
    ) == status["counts"]["question_pixels_available"]


def test_supplemental_pagination_accepts_the_contract_ceiling_and_rejects_above_it():
    reader = SupplementalVisualScanReader(SHCHEM_ROOT)
    empty = reader.list_atomic(limit=200, offset=100_000)
    assert empty["offset"] == 100_000
    assert empty["count"] == 0
    assert empty["items"] == []
    assert empty["total"] == reader.status()["counts"]["atomic_parts"]
    with pytest.raises(SupplementalVisualScanError) as error:
        reader.list_atomic(limit=200, offset=100_001)
    assert error.value.status == 400
    assert error.value.code == "supplemental_scan_pagination_invalid"


def test_supplemental_reader_caches_one_verified_registry_snapshot_for_all_dtos(
    monkeypatch,
):
    reader = SupplementalVisualScanReader(SHCHEM_ROOT)
    status = reader.status()
    cached = reader._registry_snapshot()

    def unexpected_rebuild():
        raise AssertionError("a verified immutable registry snapshot must be reused")

    monkeypatch.setattr(reader, "_build_registry_snapshot", unexpected_rebuild)
    first_id = reader.list_atomic(limit=1, offset=0)["items"][0]["node_id"]
    values = (
        reader.status(),
        reader.list_atomic(limit=200, offset=0),
        reader.detail(first_id),
        reader.theme_groups(),
    )
    assert reader._registry_snapshot() is cached
    assert {value["data_snapshot_id"] for value in values} == {
        status["data_snapshot_id"]
    }


def test_private_registry_snapshot_rejects_nested_mutation_and_dtos_stay_stable():
    reader = SupplementalVisualScanReader(SHCHEM_ROOT)
    first_page = reader.list_atomic(limit=200, offset=0)
    first_id = first_page["items"][0]["node_id"]
    before = {
        "status": reader.status(),
        "list": first_page,
        "detail": reader.detail(first_id),
        "themes": reader.theme_groups(),
    }
    snapshot = reader._registry_snapshot()
    record = snapshot.by_node_id[first_id][1]

    with pytest.raises(TypeError, match="snapshot is immutable"):
        snapshot.by_node_id[first_id] = snapshot.by_node_id[first_id]
    with pytest.raises(TypeError, match="snapshot is immutable"):
        record["scan_status"] = "tampered"
    with pytest.raises(TypeError, match="snapshot is immutable"):
        record["classification"]["A"].append("A99")
    with pytest.raises(TypeError, match="snapshot is immutable"):
        record["viewed_evidence"].clear()

    after = {
        "status": reader.status(),
        "list": reader.list_atomic(limit=200, offset=0),
        "detail": reader.detail(first_id),
        "themes": reader.theme_groups(),
    }
    assert reader._registry_snapshot() is snapshot
    assert after == before
    assert {value["data_snapshot_id"] for value in after.values()} == {
        before["status"]["data_snapshot_id"]
    }


def test_supplemental_detail_copies_source_answers_without_independent_verification():
    reader = SupplementalVisualScanReader(SHCHEM_ROOT)
    exam = reader.detail("SJ2026-EM-S1-Q1-P1")["visual_scan"]
    assert exam["reference_answer"] == {
        "availability": "present_part_aligned",
        "reference_answer_text": "BC",
        "source_authority": "nonofficial_reference",
        "independently_verified": False,
        "quality_note": None,
    }
    handout_id = next(
        item["node_id"]
        for item in reader.list_atomic(limit=200, offset=0)["items"]
        if item["source_kind"] == "external_teaching_handout"
    )
    handout = reader.detail(handout_id)["visual_scan"]
    assert handout["reference_answer"]["source_authority"] == (
        "nonofficial_teaching_handout_reference"
    )
    assert handout["reference_answer"]["independently_verified"] is False
    assert handout["answer_boundary"]["verified"] is False
    assert handout["answer_boundary"]["independently_verified"] is False


def test_all_supplemental_details_keep_source_specific_scan_status_and_snapshot():
    reader = SupplementalVisualScanReader(SHCHEM_ROOT)
    status = reader.status()
    page = reader.list_atomic(limit=200, offset=0)
    expected_scan_status = {
        "shanghai_exam_wechat_archive": "visual_scan_completed",
        "external_teaching_handout": (
            "visual_scan_completed_external_handout_isolated"
        ),
    }
    seen = Counter()
    for item in page["items"]:
        detail = reader.detail(item["node_id"])
        visual = detail["visual_scan"]
        source_kind = item["source_kind"]
        assert detail["data_snapshot_id"] == status["data_snapshot_id"]
        assert detail["node"]["node_id"] == item["node_id"]
        assert visual["source_kind"] == source_kind
        assert visual["scan_status"] == expected_scan_status[source_kind]
        assert visual["reference_answer"]["independently_verified"] is False
        seen[source_kind] += 1
    assert seen == Counter(
        {
            "shanghai_exam_wechat_archive": status["counts"][
                "shanghai_exam_atomic_parts"
            ],
            "external_teaching_handout": status["counts"][
                "external_handout_atomic_parts"
            ],
        }
    )


def test_supplemental_theme_groups_are_theme_first_and_keep_dependency_edges():
    reader = SupplementalVisualScanReader(SHCHEM_ROOT)
    status = reader.status()
    value = reader.theme_groups()
    assert value["scope"] == "supplemental"
    assert value["data_snapshot_id"] == status["data_snapshot_id"]
    status_counts = status["counts"]
    assert value["counts"] == {
        "papers": status_counts["papers"],
        "theme_groups": status_counts["theme_big_questions"],
        "atomic_parts": status_counts["atomic_parts"],
        "display_atomic_units": status_counts["atomic_parts"],
        "unassigned_atomic_parts": 0,
        "visual_scanned": status_counts["atomic_parts"],
        "unscanned": 0,
        "label_complete": status_counts["atomic_parts"],
        "label_pending": 0,
        "answer_aligned": status_counts["answer_aligned"],
        "answer_unaligned": status_counts["answer_unaligned"],
        "answer_absent": status_counts["answer_absent"],
        "quality_notes": status_counts["quality_notes"],
    }
    groups = [group for paper in value["papers"] for group in paper["theme_groups"]]
    specs = reader.product_specs
    assert [group["counts"]["atomic"] for group in groups] == [
        spec.record_count for spec in specs
    ]
    assert [group["counts"]["printed"] for group in groups] == [
        spec.printed_count for spec in specs
    ]
    assert [group["theme"]["title"] for group in groups] == [
        spec.theme_title_zh for spec in specs
    ]
    assert groups[6]["dependencies"]["explicit_prior_edge_count"] == 1
    assert groups[6]["paper"]["status"] == (
        "external_teaching_handout_partial_themes_unreviewed"
    )


def test_supplemental_source_metadata_separates_shanghai_archives_and_handouts():
    value = SupplementalVisualScanReader(SHCHEM_ROOT).theme_groups()
    papers = {
        entry["paper"]["id"]: entry["paper"]["source_metadata"]
        for entry in value["papers"]
    }
    assert papers["PAPER-09f1bd32b08e2454e0fc"] == {
        "source_tier": "shanghai_exam_wechat_archive",
        "year": "2026",
        "region": "unknown",
        "paper_type": "second_mock_nonofficial_attribution",
        "attribution_status": "unknown",
    }
    assert papers["PAPER-8da880c69c0bb53bdbb4"] == {
        "source_tier": "shanghai_exam_wechat_archive",
        "year": "2026",
        "region": "虹口区（仅文章标题归属；卷面未署区）",
        "paper_type": "second_mock_nonofficial_attribution",
        "attribution_status": "article_title_attribution_only",
    }
    assert papers["MVPPLUS-A-PL-0bb39aa129f46783"]["region"] == (
        "金山区（仅文章标题归属；卷面未署区）"
    )
    handouts = [
        metadata
        for paper_id, metadata in papers.items()
        if paper_id.startswith("HANDOUT-")
    ]
    assert len(handouts) == 2
    assert all(
        metadata == {
            "source_tier": "external_handout",
            "year": "unknown",
            "region": "unknown",
            "paper_type": "external_handout",
            "attribution_status": "not_exam_identity",
        }
        for metadata in handouts
    )


def test_supplemental_crop_route_allows_only_bound_question_or_shared_pixels():
    reader = SupplementalVisualScanReader(SHCHEM_ROOT)
    payload = reader.question_crop(
        "SJ2026-EM-S1-Q1-P1", "SJ2026-T1-Q01-QUESTION"
    )
    assert payload.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert hashlib.sha256(payload.data).hexdigest() == payload.sha256
    with pytest.raises(SupplementalVisualScanError) as answer_error:
        reader.question_crop("SJ2026-EM-S1-Q1-P1", "SJ2026-T1-Q01-ANSWER")
    assert answer_error.value.status == 403
    handout_id = next(
        item["node_id"]
        for item in reader.list_atomic(limit=200, offset=0)["items"]
        if item["source_kind"] == "external_teaching_handout"
    )
    snapshot, record = reader._find(handout_id)
    handout_crop_id = record["viewed_evidence"][0]["crop_id"]
    assert snapshot.crop_by_id[handout_crop_id]["_http_exposable"] is False
    with pytest.raises(SupplementalVisualScanError) as handout_error:
        reader.question_crop(handout_id, handout_crop_id)
    assert handout_error.value.status == 403


def test_supplemental_http_endpoints_are_authenticated_read_only_and_answer_safe():
    with tempfile.TemporaryDirectory() as temp, running_server(
        build_config(Path(temp))
    ) as server:
        status_code, status_envelope, _ = _request(
            server, "/api/v1/kb/workbench/supplemental-scans/status"
        )
        assert status_code == 200
        status_data = status_envelope["data"]
        expected_total = status_data["counts"]["atomic_parts"]
        snapshot_id = status_data["data_snapshot_id"]
        assert expected_total == 86

        list_code, list_envelope, _ = _request(
            server, "/api/v1/kb/workbench/supplemental-scans?limit=200&offset=0"
        )
        assert list_code == 200
        assert list_envelope["data"]["total"] == expected_total
        assert list_envelope["data"]["data_snapshot_id"] == snapshot_id

        empty_code, empty_envelope, _ = _request(
            server,
            "/api/v1/kb/workbench/supplemental-scans?limit=200&offset=100000",
        )
        assert empty_code == 200
        assert empty_envelope["data"]["items"] == []
        assert empty_envelope["data"]["count"] == 0
        assert empty_envelope["data"]["total"] == expected_total
        assert empty_envelope["data"]["data_snapshot_id"] == snapshot_id

        invalid_code, invalid_envelope, _ = _request(
            server,
            "/api/v1/kb/workbench/supplemental-scans?limit=200&offset=100001",
        )
        assert invalid_code == 400
        assert invalid_envelope["error"]["code"] == (
            "supplemental_scan_pagination_invalid"
        )

        detail_code, detail_envelope, _ = _request(
            server, "/api/v1/kb/workbench/supplemental-scans/SJ2026-EM-S1-Q1-P1"
        )
        assert detail_code == 200
        assert detail_envelope["data"]["data_snapshot_id"] == snapshot_id
        assert detail_envelope["data"]["visual_scan"]["reference_answer"][
            "reference_answer_text"
        ] == "BC"

        crop_code, crop, headers = _request(
            server,
            "/api/v1/kb/workbench/supplemental-scans/"
            "SJ2026-EM-S1-Q1-P1/question-crops/SJ2026-T1-Q01-QUESTION",
        )
        assert crop_code == 200
        assert crop.startswith(b"\x89PNG\r\n\x1a\n")
        assert headers.get("Cache-Control") == "no-store"

        answer_code, _, _ = _request(
            server,
            "/api/v1/kb/workbench/supplemental-scans/"
            "SJ2026-EM-S1-Q1-P1/question-crops/SJ2026-T1-Q01-ANSWER",
        )
        assert answer_code == 403

        theme_code, theme_envelope, _ = _request(
            server, "/api/v1/kb/workbench/theme-groups?scope=supplemental"
        )
        assert theme_code == 200
        assert theme_envelope["data"]["counts"]["theme_groups"] == 10
        assert theme_envelope["data"]["data_snapshot_id"] == snapshot_id


def test_supplemental_webui_exposes_a_third_chinese_scope_without_merging_counts():
    html = (OVERLAY / "index.html").read_text(encoding="utf-8")
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    assert '<select id="candidateReviewScope" disabled>' in html

    handshake = app[
        app.index("function validateWorkbenchReleaseHandshake") :
        app.index("function shortWorkbenchReleaseId")
    ]
    for marker in (
        'readiness.combined_atomic_total !== null',
        'readiness.cross_scope_sum_allowed !== false',
        'registry.combined_atomic_total !== null',
        'registry.cross_scope_sum_allowed !== false',
        'product.non_additive_to',
        'release.productOrder.filter((scope) => scope !== expectedScope)',
    ):
        assert marker in handshake

    renderer = app[
        app.index("function renderWorkbenchReleaseHandshake") :
        app.index("function modelProviderSafeId")
    ]
    for marker in (
        'scopeSelect.replaceChildren(...registry.products.map',
        '${product.display_name_zh}（${product.counts.atomic_parts} 题）',
        'product.scope',
        'registry.scope_boundary_zh',
    ):
        assert marker in renderer

    assert 'api("/api/v1/workbench/product-registry")' in app
    assert 'productOrder: Object.freeze(["wave1", "master", "supplemental"])' in app
    for marker in (
        'scope: "supplemental"',
        "expectedTotal: product?.counts?.atomic_parts ?? null",
        "validateCandidateReviewSupplementalStatusHeader",
        "candidateReviewThemeExpected",
        "data_snapshot_id",
        "计数来自冻结注册表",
        "validateAndRenderSupplementalCandidateReviewStatus",
        "validateCandidateReviewSupplementalScanDetail",
        "补充资料逐图整理",
        "用户讲义 · 未审定 · 不是上海原题",
        "/api/v1/kb/workbench/supplemental-scans/",
        "must_not_be_added_to_master470",
        "nonofficial_teaching_handout_reference",
    ):
        assert marker in app
    for legacy in (
        '<option value="supplemental">补充资料（区模与讲义·65题）</option>',
        "expectedTotal: 65",
        "补充资料 65 题",
        "上海区模公众号存档 39 题、用户讲义 26 题",
    ):
        assert legacy not in html + app


def test_supplemental_webui_accepts_only_the_source_specific_detail_scan_status():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    validator = app[
        app.index("function validateCandidateReviewSupplementalScanDetail") :
        app.index("function renderCandidateReviewVisualScan")
    ]
    assert 'item.source_kind === "external_teaching_handout"' in validator
    assert '"visual_scan_completed_external_handout_isolated"' in validator
    assert ': "visual_scan_completed"' in validator
    assert '"not_applicable_no_prior_atomic_labels"' in validator
    assert "comparison.prior_candidate_available !== false" in validator
    renderer = app[
        app.index("function renderCandidateReviewVisualComparison") :
        app.index("function validateCandidateReviewSupplementalScanDetail")
    ]
    assert "无可比逐题候选" in renderer
    assert "未伪造旧标签或比较结论" in renderer


def test_supplemental_product_specs_are_registry_backed_and_not_copied_into_ui():
    reader = SupplementalVisualScanReader(SHCHEM_ROOT)
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    specs = reader.product_specs
    assert len(specs) == registry["product_count"]
    assert [spec.product_id for spec in specs] == [
        product["product_id"] for product in registry["products"]
    ]
    for spec in specs:
        assert spec.manifest_file_sha256 not in app


def test_overlay_manifest_declares_the_non_additive_supplemental_scope():
    expected = {
        "scope": "candidate_only_read_only_supplemental_visual_scan",
        "status_endpoint": "/api/v1/kb/workbench/supplemental-scans/status",
        "list_endpoint": "/api/v1/kb/workbench/supplemental-scans",
        "detail_endpoint_template": "/api/v1/kb/workbench/supplemental-scans/{node_id}",
        "question_crop_endpoint_template": (
            "/api/v1/kb/workbench/supplemental-scans/{node_id}/question-crops/{crop_id}"
        ),
        "registry_id": REGISTRY_ID,
        "registry_schema_version": REGISTRY_SCHEMA_VERSION,
        "registry_file_sha256": REGISTRY_FILE_SHA256,
        "registry_schema_sha256": REGISTRY_SCHEMA_SHA256,
        "registry_self_sha256": "d2aaee8420c1d8681ff95a3a35965e1de70c07b056fc2e2c481ea9960feb4b35",
        "counts_source": "runtime_verified_registry_snapshot",
        "dynamic_counts": True,
        "non_additive_to_master470": True,
        "non_additive_to_wave252": True,
        "handout_is_not_shanghai_exam": True,
        "source_answers_independently_verified": False,
        "answer_pixels_exposed": False,
        "teacher_only": True,
        "read_only": True,
        "candidate_only": True,
    }
    manifests = [OVERLAY / "overlay.manifest.json", OVERLAY.parent / "overlay.manifest.json"]
    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["supplemental_visual_scan"] == expected


def test_openapi_strictly_validates_all_live_supplemental_dtos():
    reader = SupplementalVisualScanReader(SHCHEM_ROOT)
    contract = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    page = reader.list_atomic(limit=200, offset=0)
    values = {
        "SupplementalScanStatusData": reader.status(),
        "SupplementalScanListData": page,
        "ThemeWorkbenchData": reader.theme_groups(),
    }
    for schema_name, value in values.items():
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
    detail_validator = Draft202012Validator(
        {
            "$ref": "#/components/schemas/SupplementalScanDetailData",
            "components": contract["components"],
        }
    )
    for item in page["items"]:
        value = reader.detail(item["node_id"])
        errors = list(detail_validator.iter_errors(value))
        assert errors == [], [
            {
                "node_id": item["node_id"],
                "path": list(error.absolute_path),
                "message": error.message,
            }
            for error in errors[:10]
        ]


def test_openapi_rejects_nested_supplemental_list_and_detail_mutations():
    reader = SupplementalVisualScanReader(SHCHEM_ROOT)
    contract = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    list_validator = Draft202012Validator(
        {
            "$ref": "#/components/schemas/SupplementalScanListData",
            "components": contract["components"],
        }
    )
    detail_validator = Draft202012Validator(
        {
            "$ref": "#/components/schemas/SupplementalScanDetailData",
            "components": contract["components"],
        }
    )
    page = reader.list_atomic(limit=200, offset=0)
    exam_item = next(
        item
        for item in page["items"]
        if item["source_kind"] == "shanghai_exam_wechat_archive"
        and item["crop_refs"]["count"] > 0
    )
    handout_item = next(
        item
        for item in page["items"]
        if item["source_kind"] == "external_teaching_handout"
    )
    exam_detail = reader.detail(exam_item["node_id"])
    handout_detail = reader.detail(handout_item["node_id"])

    list_mutations = {}

    value = copy.deepcopy(page)
    value["items"][0]["classification"] = {}
    list_mutations["classification_empty"] = value

    value = copy.deepcopy(page)
    value["items"][0]["parent_chain"][0]["node_type"] = "atomic_part"
    list_mutations["parent_chain_wrong_level"] = value

    value = copy.deepcopy(page)
    value["items"][0]["difficulty"]["factors"].pop()
    list_mutations["difficulty_missing_one_of_ten_factors"] = value

    value = copy.deepcopy(page)
    crop_item = next(
        item for item in value["items"] if item["node_id"] == exam_item["node_id"]
    )
    crop_item["crop_refs"]["paths_exposed"] = True
    list_mutations["crop_refs_exposes_paths"] = value

    for name, mutated in list_mutations.items():
        errors = list(list_validator.iter_errors(mutated))
        assert errors, f"OpenAPI accepted supplemental list mutation: {name}"

    detail_mutations = {}

    value = copy.deepcopy(exam_detail)
    value["visual_scan"]["dependency"]["dependency_kind"] = "fabricated"
    detail_mutations["dependency_kind_uncontrolled"] = value

    value = copy.deepcopy(exam_detail)
    value["visual_scan"]["scan_classification"] = {}
    detail_mutations["scan_classification_empty"] = value

    value = copy.deepcopy(exam_detail)
    value["visual_scan"]["cognitive_difficulty"]["factors"].pop()
    detail_mutations["cognitive_difficulty_missing_factor"] = value

    value = copy.deepcopy(exam_detail)
    value["visual_scan"]["scan_status"] = (
        "visual_scan_completed_external_handout_isolated"
    )
    detail_mutations["exam_source_kind_scan_status_drift"] = value

    value = copy.deepcopy(handout_detail)
    value["visual_scan"]["scan_status"] = "visual_scan_completed"
    detail_mutations["handout_source_kind_scan_status_drift"] = value

    value = copy.deepcopy(exam_detail)
    answer = value["visual_scan"]["reference_answer"]
    answer["availability"] = "absent"
    detail_mutations["absent_answer_keeps_text_and_authority"] = value

    value = copy.deepcopy(handout_detail)
    value["visual_scan"]["reference_answer"]["source_authority"] = (
        "nonofficial_reference"
    )
    detail_mutations["handout_answer_authority_drift"] = value

    for name, mutated in detail_mutations.items():
        errors = list(detail_validator.iter_errors(mutated))
        assert errors, f"OpenAPI accepted supplemental detail mutation: {name}"
