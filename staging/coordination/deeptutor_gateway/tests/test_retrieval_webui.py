from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1.config import Principal, token_digest
from integrations.deeptutor_shchem_v1.retrieval_workbench import (
    KB_RETRIEVAL_READ_CAPABILITY,
    RetrievalWorkbenchGateway,
)
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    OVERLAY,
    SHCHEM_ROOT,
    build_config,
    request,
    running_server,
)

WORKSPACE = Path(__file__).resolve().parents[4]
OPENAPI = Path(__file__).resolve().parents[1] / "contracts/gateway_openapi_v1.yaml"
READ_TOKEN = "retrieval-teacher-read-0123456789"
NO_CAP_TOKEN = "retrieval-teacher-no-cap-0123456789"
STUDENT_TOKEN = "retrieval-student-0123456789"
PINNED_SOURCES = (
    SHCHEM_ROOT / "kb/retrieval/index.sqlite3",
    SHCHEM_ROOT / "kb/retrieval/config.json",
    SHCHEM_ROOT / "kb/retrieval/retrieval_core.py",
)


def build_retrieval_config(state_root: Path):
    config = build_config(state_root)
    config.principals = [
        Principal(
            "retrieval-teacher-read",
            "teacher",
            token_digest(READ_TOKEN),
            (),
            (KB_RETRIEVAL_READ_CAPABILITY,),
        ),
        Principal(
            "retrieval-teacher-no-cap",
            "teacher",
            token_digest(NO_CAP_TOKEN),
        ),
        Principal(
            "retrieval-student",
            "student",
            token_digest(STUDENT_TOKEN),
        ),
    ]
    config.students = ()
    config.validate()
    return config


def origin(server) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"


def trusted_headers(server) -> dict[str, str]:
    return {"Origin": origin(server), "Sec-Fetch-Site": "same-origin"}


def source_state(path: Path) -> tuple[str, int, int]:
    value = path.stat()
    return (
        hashlib.sha256(path.read_bytes()).hexdigest(),
        value.st_size,
        value.st_mtime_ns,
    )


def assert_retrieval_boundaries(value: dict):
    assert value["read_only"] is True
    assert value["snapshot_verified"] is True
    assert value["content_exposed"] is False
    assert value["source_file_exposed"] is False
    assert value["human_reviewed"] is False
    serialized = json.dumps(value, ensure_ascii=False).casefold()
    for forbidden in (
        "c:\\users",
        "file://",
        "private_state",
        "private_runtime",
        "06_学生错题档案",
        "source_path",
        "record_path",
    ):
        assert forbidden not in serialized


def test_actual_pinned_db_search_is_teacher_capability_scoped_and_read_only():
    before = {path: source_state(path) for path in PINNED_SOURCES}
    with tempfile.TemporaryDirectory() as temp, running_server(
        build_retrieval_config(Path(temp))
    ) as server:
        status, status_body, _ = request(
            server,
            "GET",
            "/api/v1/kb/retrieval/status",
            token=READ_TOKEN,
        )
        assert status == 200
        capability = status_body["data"]
        assert capability["status"] == "ready"
        assert capability["read_capability_required"] == KB_RETRIEVAL_READ_CAPABILITY
        assert capability["read_capability_granted"] is True
        assert capability["snapshot_verified"] is False
        assert capability["write_endpoint_present"] is False
        assert capability["ingest_endpoint_present"] is False
        assert capability["rebuild_endpoint_present"] is False
        assert capability["official_claim_upgrade_present"] is False

        status, body, _ = request(
            server,
            "POST",
            "/api/v1/kb/retrieval/search",
            token=READ_TOKEN,
            headers=trusted_headers(server),
            payload={"query": "2024", "purpose": "general_research", "limit": 3},
        )
        assert status == 200, body
        value = body["data"]
        assert value["count"] <= 3
        assert value["snapshot"] == {
            "config": {
                "bytes": 7998,
                "sha256": "8a2160d5f2518eda187dc1d49e678fec925b07eb1c4c737b8210fa4aa8cf272f",
            },
            "index_db": {
                "bytes": 2367488,
                "sha256": "ede1daef065967446156fcf98c461a988f48d6a1f4f985681288c6fdfeee82ca",
            },
            "retrieval_core": {
                "bytes": 93593,
                "sha256": "1860e4456663c19b35cf8dc5dd139c4e4dbaf62d872ae320ed0823fc1981ad97",
            },
        }
        assert_retrieval_boundaries(value)
    assert {path: source_state(path) for path in PINNED_SOURCES} == before


def test_anonymous_student_no_cap_and_untrusted_origin_fail_closed():
    with tempfile.TemporaryDirectory() as temp, running_server(
        build_retrieval_config(Path(temp))
    ) as server:
        endpoint = "/api/v1/kb/retrieval/search"
        payload = {"query": "2024", "purpose": "general_research"}
        status, body, _ = request(
            server,
            "POST",
            endpoint,
            token=None,
            headers=trusted_headers(server),
            payload=payload,
        )
        assert status == 401
        assert body["error"]["code"] == "authentication_required"

        for token, code in (
            (STUDENT_TOKEN, "teacher_scope_required"),
            (NO_CAP_TOKEN, "kb_retrieval_read_capability_required"),
        ):
            status, body, _ = request(
                server,
                "POST",
                endpoint,
                token=token,
                headers=trusted_headers(server),
                payload=payload,
            )
            assert status == 403
            assert body["error"]["code"] == code

        status, body, _ = request(
            server, "POST", endpoint, token=READ_TOKEN, payload=payload
        )
        assert status == 403
        assert body["error"]["code"] == "origin_required"
        status, body, _ = request(
            server,
            "POST",
            endpoint,
            token=READ_TOKEN,
            headers={"Origin": "https://evil.example"},
            payload=payload,
        )
        assert status == 403
        assert body["error"]["code"] == "origin_denied"


@pytest.mark.parametrize(
    "forbidden_field",
    ["path", "db", "database", "config", "workspace_root", "include_excluded"],
)
def test_browser_cannot_supply_server_fixed_or_excluded_inputs(forbidden_field):
    with tempfile.TemporaryDirectory() as temp, running_server(
        build_retrieval_config(Path(temp))
    ) as server:
        status, body, _ = request(
            server,
            "POST",
            "/api/v1/kb/retrieval/search",
            token=READ_TOKEN,
            headers=trusted_headers(server),
            payload={
                "query": "2024",
                "purpose": "general_research",
                forbidden_field: True,
            },
        )
        assert status == 400
        assert body["error"]["code"] == "UNKNOWN_FIELD"
        assert body["error"]["details"]["unknown_field_count"] == 1
        assert body["error"]["details"]["retrieval_boundary"] == {
            "read_only": True,
            "snapshot_verified": False,
            "content_exposed": False,
            "source_file_exposed": False,
            "human_reviewed": False,
        }


def test_official_scope_year_and_question_reuse_policy_reach_core_unchanged():
    with tempfile.TemporaryDirectory() as temp, running_server(
        build_retrieval_config(Path(temp))
    ) as server:
        endpoint = "/api/v1/kb/retrieval/search"
        headers = trusted_headers(server)
        status, body, _ = request(
            server,
            "POST",
            endpoint,
            token=READ_TOKEN,
            headers=headers,
            payload={"query": "2026", "purpose": "official_claim"},
        )
        assert status == 400
        assert body["error"]["code"] == "OFFICIAL_CLAIM_SCOPE_REQUIRED"

        status, body, _ = request(
            server,
            "POST",
            endpoint,
            token=READ_TOKEN,
            headers=headers,
            payload={
                "query": "2026",
                "purpose": "official_claim",
                "source_family": "official_exam_schedule",
                "authority_scope": "exam_date_and_duration",
                "claim_year": 2025,
            },
        )
        assert status == 400
        assert body["error"]["code"] == "OFFICIAL_CLAIM_YEAR_CONFLICT"

        status, body, _ = request(
            server,
            "POST",
            endpoint,
            token=READ_TOKEN,
            headers=headers,
            payload={
                "query": "2026",
                "purpose": "official_claim",
                "source_family": "official_exam_schedule",
                "authority_scope": "exam_date_and_duration",
                "claim_year": 2026,
            },
        )
        assert status == 200
        assert body["data"]["count"] == 1
        assert "exam_total_score" in body["data"]["preflight"]["blocked_claims"]
        assert_retrieval_boundaries(body["data"])

        status, body, _ = request(
            server,
            "POST",
            endpoint,
            token=READ_TOKEN,
            headers=headers,
            payload={"query": "2024", "purpose": "question_reuse", "limit": 5},
        )
        assert status == 200
        assert body["data"]["status"] == "denied"
        assert body["data"]["results"] == []
        assert any(
            "copyright_use=" in denial
            for denial in body["data"]["preflight"]["denials"]
        )
        assert_retrieval_boundaries(body["data"])


def test_fake_gateway_projection_recursively_removes_host_and_private_paths():
    class FakeGateway:
        def query(self, payload):
            return {
                "status": "ok",
                "count": 1,
                "results": [
                    {
                        "record_id": "SAFE-001",
                        "title": "Safe candidate",
                        "preview": "bounded preview",
                        "source_path": r"C:\Users\name\private_state\source.pdf",
                        "nested": {
                            "innocent": r"C:\Users\name\source.pdf",
                            "student_payload": "secret",
                            "human_reviewed": True,
                        },
                    }
                ],
                "debug": {"record_path": "/home/user/raw.json"},
                "read_only": False,
                "content_exposed": True,
                "source_file_exposed": True,
                "human_reviewed": True,
                "snapshot_verified": True,
            }

    adapter = RetrievalWorkbenchGateway(WORKSPACE, gateway=FakeGateway())
    value = adapter.search({"query": "safe", "purpose": "general_research"})
    assert_retrieval_boundaries(value)
    assert value["results"][0]["title"] == "Safe candidate"
    assert value["results"][0]["nested"] == {"human_reviewed": False}
    assert value["debug"] == {}


def test_overlay_request_allowlist_role_reset_responsive_layout_and_copy():
    html = (OVERLAY / "index.html").read_text(encoding="utf-8")
    script = (OVERLAY / "app.js").read_text(encoding="utf-8")
    css = (OVERLAY / "styles.css").read_text(encoding="utf-8")
    for marker in (
        'id="tab-retrieval"',
        'id="panel-retrieval"',
        'id="retrievalForm"',
        'id="retrievalQuery"',
        'id="retrievalPurpose"',
        'id="retrievalLimit"',
        'id="retrievalK"',
        'id="retrievalA"',
        'id="retrievalC"',
        'id="retrievalR"',
        'id="retrievalD"',
        'id="retrievalStrict"',
        'id="retrievalSourceFamily"',
        'id="retrievalTemporalRole"',
        'id="retrievalOfficialOnly"',
        'id="retrievalAuthorityScope"',
        'id="retrievalClaimYear"',
        "候选证据，不是全文",
        "未完成人工复核",
        "不会自动进入命题",
    ):
        assert marker in html

    start = script.index("function retrievalPayload()")
    end = script.index("function retrievalPreflightLines", start)
    request_source = script[start:end]
    for allowed in (
        "query",
        "purpose",
        "limit",
        "K",
        "A",
        "C",
        "R",
        "D",
        "strict_tags",
        "source_family",
        "temporal_role",
        "official_only",
        "authority_scope",
        "claim_year",
    ):
        assert allowed in request_source
    for forbidden in (
        "path:",
        "workspace_root",
        "database",
        "config:",
        "include_excluded",
    ):
        assert forbidden not in request_source

    reset_start = script.index("function clearTeacherDerivedState()")
    reset_end = script.index("function setRoute()", reset_start)
    reset_source = script[reset_start:reset_end]
    for marker in (
        "state.retrievalReadAllowed = false",
        "state.retrievalStatusLoaded = false",
        'resetBlock("retrievalResults"',
        'resetBlock("retrievalPreflight"',
        '"retrievalQuery",',
        '$(id).value = ""',
    ):
        assert marker in reset_source
    assert 'api("/api/v1/kb/retrieval/search"' in script
    assert "/api/v1/kb/retrieval/ingest" not in script
    assert "/api/v1/kb/retrieval/rebuild" not in script
    assert "/api/v1/wechat" not in script
    for viewport in (375, 768, 1024):
        assert viewport > 0
        assert "minmax(0, 1fr)" in css
        assert "max-width: 100%" in css
        assert "overflow-wrap: anywhere" in css
    assert ".retrieval-grid" in css
    assert ".retrieval-output-grid" in css
    assert "@media (max-width: 760px)" in css


def test_openapi_and_manifests_bind_read_only_retrieval_contract():
    contract = OPENAPI.read_text(encoding="utf-8")
    assert "/api/v1/kb/retrieval/status:" in contract
    assert "/api/v1/kb/retrieval/search:" in contract
    assert "RetrievalBrowserRequest" in contract
    assert "additionalProperties: false" in contract
    assert "kb_retrieval_read" in contract
    for forbidden_route in (
        "/api/v1/kb/retrieval/ingest",
        "/api/v1/kb/retrieval/write",
        "/api/v1/kb/retrieval/rebuild",
        "/api/v1/kb/retrieval/wechat",
        "/api/v1/kb/retrieval/official-upgrade",
    ):
        assert forbidden_route not in contract

    manifest = json.loads((OVERLAY / "overlay.manifest.json").read_text(encoding="utf-8"))
    retrieval = manifest["retrieval_slice1"]
    assert retrieval["teacher_only"] is True
    assert retrieval["read_capability_required"] == KB_RETRIEVAL_READ_CAPABILITY
    assert retrieval["read_only"] is True
    assert retrieval["content_exposed"] is False
    assert retrieval["source_file_exposed"] is False
    assert retrieval["human_reviewed"] is False
    assert retrieval["automatic_generation_input"] is False
    assert retrieval["write_endpoint_present"] is False
    assert retrieval["ingest_endpoint_present"] is False
    assert retrieval["wechat_endpoint_present"] is False
    assert retrieval["rebuild_endpoint_present"] is False
    assert retrieval["official_claim_upgrade_present"] is False
    for name in ("index.html", "app.js", "styles.css"):
        digest = hashlib.sha256((OVERLAY / name).read_bytes()).hexdigest()
        assert manifest["files"][name]["sha256"] == digest
