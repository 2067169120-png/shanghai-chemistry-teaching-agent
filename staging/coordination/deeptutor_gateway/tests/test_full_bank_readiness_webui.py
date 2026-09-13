from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1.config import Principal, token_digest
from integrations.deeptutor_shchem_v1.full_bank_readiness import (
    REPORT_RELATIVE,
    FullBankReadinessError,
    FullBankReadinessReader,
)
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    build_config,
    request,
    running_server,
)


WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
OPENAPI = WORKSPACE / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
STUDENT_TOKEN = "full-bank-readiness-student-0123456789"

SOURCE_PATHS = (
    "kb/paper_learning_v1/paper_inventory.jsonl",
    "kb/paper_learning_v1/profiles/index.jsonl",
    "kb/paper_learning_v1/reports/formalization_queue.json",
    "kb/formal/candidates/wave1_formalization_2026-08-04/paper_records.jsonl",
    ".intake/2026-07-30-user-teaching-pack/analysis/pack_inventory.jsonl",
)


def canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def browser_headers(server) -> dict[str, str]:
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    return {"Origin": origin, "Sec-Fetch-Site": "same-origin"}


def isolated_root(temp: Path, *, include_sources: bool) -> Path:
    root = temp / "sh-chem-db"
    report_target = root / REPORT_RELATIVE
    report_target.parent.mkdir(parents=True)
    shutil.copy2(SHCHEM_ROOT / REPORT_RELATIVE, report_target)
    if include_sources:
        for relative in SOURCE_PATHS:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SHCHEM_ROOT / relative, target)
    return root


def source_snapshot() -> dict[str, tuple[int, int, str]]:
    paths = [SHCHEM_ROOT / REPORT_RELATIVE, *(SHCHEM_ROOT / item for item in SOURCE_PATHS)]
    return {
        path.relative_to(SHCHEM_ROOT).as_posix(): (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in paths
    }


def test_reader_keeps_two_cohorts_distinct_and_zero_formal_ready():
    reader = FullBankReadinessReader(SHCHEM_ROOT)
    status = reader.status()
    counts = status["counts"]
    assert counts["paper_inventory_records"] == 80
    assert counts["teaching_package_records"] == 98
    assert counts["total_queue_records"] == 178
    assert counts["formal_question_ready_records"] == 0
    assert counts["paper_four_layer_hash_verified_inventory_matches"] == 0
    assert counts["paper_four_layer_url_match_crosswalk_candidates"] == 3
    assert status["integrity"]["source_bindings_verified"] is True
    assert status["authority"]["mutation_endpoint_present"] is False
    assert "两个独立 cohort" in status["cohort_boundary"]
    assert "178 份试卷" in status["cohort_boundary"]


def test_record_projection_is_bounded_and_exposes_no_content_or_paths():
    reader = FullBankReadinessReader(SHCHEM_ROOT)
    papers = reader.list_records(
        cohort="shanghai_paper_inventory",
        stage=None,
        query=None,
        limit=200,
        offset=0,
    )
    teaching = reader.list_records(
        cohort="quarantined_user_teaching_pack",
        stage=None,
        query=None,
        limit=200,
        offset=0,
    )
    assert papers["total"] == 80
    assert teaching["total"] == 98
    assert papers["content_exposed"] is False
    assert teaching["source_paths_exposed"] is False
    assert all(item["formal_question_ready"] is False for item in papers["items"])
    assert all(item["formal_question_ready"] is False for item in teaching["items"])
    assert all(any("\u4e00" <= char <= "\u9fff" for char in item["next_action"]) for item in papers["items"])
    assert all(any("\u4e00" <= char <= "\u9fff" for char in item["next_action"]) for item in teaching["items"])
    serialized = json.dumps({"papers": papers, "teaching": teaching}, ensure_ascii=False)
    for forbidden in (
        '"source_path":',
        '"manifest_path":',
        '"documents":',
        ".docx",
        "C:\\Users",
        "question_text",
        "answer_text",
    ):
        assert forbidden not in serialized


def test_self_source_and_authority_mutations_fail_closed():
    with tempfile.TemporaryDirectory() as temp_name:
        root = isolated_root(Path(temp_name), include_sources=False)
        report = root / REPORT_RELATIVE
        value = json.loads(report.read_text(encoding="utf-8"))
        value["counts"]["formal_question_ready_records"] = 1
        report.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(FullBankReadinessError) as captured:
            FullBankReadinessReader(root).status()
        assert captured.value.code == "full_bank_readiness_self_hash_mismatch"

    with tempfile.TemporaryDirectory() as temp_name:
        root = isolated_root(Path(temp_name), include_sources=True)
        FullBankReadinessReader(root).status()
        source = root / SOURCE_PATHS[0]
        source.write_bytes(source.read_bytes() + b"\n")
        with pytest.raises(FullBankReadinessError) as captured:
            FullBankReadinessReader(root).status()
        assert captured.value.code == "full_bank_readiness_source_hash_mismatch"

    with tempfile.TemporaryDirectory() as temp_name:
        root = isolated_root(Path(temp_name), include_sources=True)
        report = root / REPORT_RELATIVE
        value = json.loads(report.read_text(encoding="utf-8"))
        value["paper_queue"][0]["gates"]["human_reviewed"] = True
        clone = dict(value)
        clone.pop("self_hash")
        value["self_hash"] = canonical_sha256(clone)
        report.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(FullBankReadinessError) as captured:
            FullBankReadinessReader(root).status()
        assert captured.value.code == "full_bank_readiness_authority_escalation"


def test_http_is_teacher_origin_scoped_read_only_and_does_not_touch_sources():
    with tempfile.TemporaryDirectory() as temp_name:
        config = build_config(Path(temp_name))
        config.principals.append(
            Principal("readiness-student", "student", token_digest(STUDENT_TOKEN), ())
        )
        before = source_snapshot()
        with running_server(config) as server:
            route = "/api/v1/kb/full-bank-readiness/status"
            status, body, _ = request(server, "GET", route, token=None)
            assert status == 401
            assert body["error"]["code"] == "authentication_required"
            status, body, _ = request(
                server, "GET", route, token=STUDENT_TOKEN, headers=browser_headers(server)
            )
            assert status == 403
            assert body["error"]["code"] == "teacher_scope_required"
            status, body, _ = request(server, "GET", route)
            assert status == 403
            assert body["error"]["code"] == "origin_required"
            status, body, _ = request(server, "GET", route, headers=browser_headers(server))
            assert status == 200
            assert body["data"]["counts"]["formal_question_ready_records"] == 0

            records = "/api/v1/kb/full-bank-readiness/records"
            status, body, _ = request(
                server,
                "GET",
                records + "?cohort=shanghai_paper_inventory&limit=2",
                headers=browser_headers(server),
            )
            assert status == 200
            assert body["data"]["count"] == 2
            status, body, _ = request(
                server,
                "GET",
                records + "?cohort=shanghai_paper_inventory&cohort=quarantined_user_teaching_pack",
                headers=browser_headers(server),
            )
            assert status == 400
            assert body["error"]["code"] == "full_bank_readiness_query_ambiguous"
            status, body, _ = request(
                server,
                "POST",
                records,
                headers=browser_headers(server),
                payload={"formal_question_ready": True},
            )
            assert status == 404
            assert body["error"]["code"] == "route_not_found"
        assert source_snapshot() == before


def test_webui_contract_shows_readiness_not_completion_and_has_no_mutation_surface():
    html = (OVERLAY / "index.html").read_text(encoding="utf-8")
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    manifest = json.loads((OVERLAY / "overlay.manifest.json").read_text(encoding="utf-8"))
    root_manifest = json.loads(
        (WORKSPACE / "runtime/deeptutor_shchem/overlay.manifest.json").read_text(
            encoding="utf-8"
        )
    )
    for marker in (
        'id="fullBankReadinessStatus"',
        'id="fullBankPaperCount"',
        'id="fullBankTeachingCount"',
        'id="fullBankFormalReadyCount"',
        "两个独立 cohort",
        "不是正式题库完成度",
    ):
        assert marker in html
    assert 'api("/api/v1/kb/full-bank-readiness/status")' in app
    assert "/api/v1/kb/full-bank-readiness/records" in app
    assert "content_exposed: value.content_exposed" in app
    assert "source_paths_exposed: value.source_paths_exposed" in app
    assert "fullBankReadinessApply" not in html + app
    for filename, descriptor in manifest["files"].items():
        assert hashlib.sha256((OVERLAY / filename).read_bytes()).hexdigest() == descriptor[
            "sha256"
        ]
    readiness = manifest["full_bank_readiness"]
    assert readiness["expected_counts"] == {
        "paper_inventory_records": 80,
        "teaching_package_records": 98,
        "total_queue_records": 178,
        "formal_question_ready_records": 0,
    }
    assert readiness["mutation_endpoint_present"] is False
    assert root_manifest["full_bank_readiness"]["two_distinct_cohorts"] is True
    contract = OPENAPI.read_text(encoding="utf-8")
    assert "/api/v1/kb/full-bank-readiness/status" in contract
    assert "/api/v1/kb/full-bank-readiness/records" in contract
