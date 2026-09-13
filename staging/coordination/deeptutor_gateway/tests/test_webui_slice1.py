from __future__ import annotations

import hashlib
import http.client
import json
import tempfile
import threading
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path

from integrations.deeptutor_shchem_v1.config import (
    AppConfig,
    Principal,
    token_digest,
)
from integrations.deeptutor_shchem_v1.http_app import create_server
from integrations.deeptutor_shchem_v1.public_kb import (
    GenerationRunReader,
    PublicKBReader,
)
from integrations.deeptutor_shchem_v1.service import (
    _status_sensitive_string,
    _status_string_variants,
)

WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
FIXTURE = WORKSPACE / "staging/coordination/deeptutor_gateway/fixtures/mock_gateway_v1.json"
TEACHER_TOKEN = "slice1-teacher-token-0123456789"
STUDENT_TOKEN = "slice1-student-token-0123456789"
STUDENT_ID = "00000000-0000-4000-8000-000000000001"


def _json_data(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")


def build_generation_fixture(workspace: Path, mutation: str | None = None) -> Path:
    shchem_root = workspace / "sh-chem-db"
    shchem_root.mkdir(parents=True)
    payloads = {}
    for logical_path in GenerationRunReader.CONTROLLED_PATHS:
        if logical_path in GenerationRunReader.REQUIRED_QA_PATHS:
            status = "fail" if mutation == "qa_fail" and logical_path.endswith("schema.json") else "pass"
            payloads[logical_path] = _json_data(
                {
                    "check": Path(logical_path).stem,
                    "status": status,
                    "errors": ["synthetic_qa_failure"] if status == "fail" else [],
                }
            )
        elif logical_path == GenerationRunReader.PREFREEZE_PATH:
            payloads[logical_path] = _json_data(
                {"version_id": "SYNTH-R18", "human_reviewed": False}
            )
        elif logical_path == GenerationRunReader.TASK_PATH:
            payloads[logical_path] = _json_data(
                {"version_id": "SYNTH-R18", "paper_id": "SYNTH-PAPER"}
            )
        elif logical_path == GenerationRunReader.PAPER_PATH:
            payloads[logical_path] = _json_data(
                {
                    "version_id": "SYNTH-R18",
                    "paper_id": "SYNTH-PAPER",
                    "themes": [],
                    "human_reviewed": False,
                    "publication_allowed": False,
                }
            )
        elif logical_path == GenerationRunReader.DELIVERY_PATH:
            payloads[logical_path] = _json_data(
                {
                    "content_status": "candidate",
                    "next_required": ["external_release_interface_closed"],
                }
            )
        elif logical_path.endswith((".png", ".svg")):
            payloads[logical_path] = b"synthetic-public-artifact"
        else:
            payloads[logical_path] = _json_data({"synthetic_fixture": True})
        path = workspace.joinpath(*logical_path.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payloads[logical_path])

    bindings = [
        {
            "logical_path": logical_path,
            "bytes": len(payloads[logical_path]),
            "sha256": hashlib.sha256(payloads[logical_path]).hexdigest(),
        }
        for logical_path in GenerationRunReader.CONTROLLED_PATHS
    ]
    if mutation == "binding_missing":
        bindings.pop()
    elif mutation == "binding_duplicate":
        bindings.append(dict(bindings[0]))
    elif mutation == "binding_extra":
        bindings.append(
            {
                "logical_path": "staging/v1_generation/reports/r18/unexpected.json",
                "bytes": 2,
                "sha256": hashlib.sha256(b"{}").hexdigest(),
            }
        )
    elif mutation == "binding_bytes":
        bindings[0]["bytes"] += 1
    elif mutation == "binding_hash":
        bindings[0]["sha256"] = "0" * 64

    verification = {
        "status": "pass_prefreeze_v2_double_clean",
        "controlled_files": bindings,
    }
    canonical = json.dumps(
        verification,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    verification["self_hash"] = hashlib.sha256(canonical).hexdigest()
    if mutation == "self_hash_bad":
        verification["self_hash"] = "f" * 64
    verification_path = workspace.joinpath(
        *GenerationRunReader.VERIFICATION_PATH.split("/")
    )
    verification_path.parent.mkdir(parents=True, exist_ok=True)
    verification_path.write_bytes(_json_data(verification))

    if mutation == "live_drift":
        drift_path = workspace.joinpath(
            *GenerationRunReader.CONTROLLED_PATHS[0].split("/")
        )
        drift_path.write_bytes(b"drifted-synthetic-public-artifact")
    return shchem_root


def build_config(state_root: Path) -> AppConfig:
    config = AppConfig(
        bind_host="127.0.0.1",
        port=0,
        mode="mock",
        state_root=state_root,
        shchem_root=SHCHEM_ROOT,
        overlay_root=OVERLAY,
        fixture_path=FIXTURE,
        principals=[
            Principal("teacher", "teacher", token_digest(TEACHER_TOKEN), (STUDENT_ID,)),
            Principal("student", "student", token_digest(STUDENT_TOKEN), (STUDENT_ID,)),
        ],
        students=(STUDENT_ID,),
    )
    config.validate()
    return config


@contextmanager
def running_server(config: AppConfig):
    server = create_server(config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(server, method: str, path: str, *, token: str | None = TEACHER_TOKEN, payload=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers.update({"Content-Type": "application/json", "Content-Length": str(len(body))})
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    data = response.read()
    content_type = response.getheader("Content-Type") or ""
    connection.close()
    return response.status, json.loads(data.decode("utf-8")) if "json" in content_type else data


class MarkupInventory(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tabs = []
        self.panels = []
        self.inline_scripts = 0
        self.live_regions = []
        self.alert_ids = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("role") == "tab":
            self.tabs.append(values)
        if values.get("role") == "tabpanel":
            self.panels.append(values)
        if tag == "script" and not values.get("src"):
            self.inline_scripts += 1
        if values.get("aria-live") in {"polite", "assertive"}:
            self.live_regions.append(values)
        if values.get("role") == "alert":
            self.alert_ids.append(values.get("id"))


class TestWebUISlice1:
    def test_openapi_endpoint_inventory_is_read_only(self):
        contract = (WORKSPACE / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml").read_text(encoding="utf-8")
        for route in (
            "/api/v1/dashboard/status",
            "/api/v1/kb/taxonomy",
            "/api/v1/kb/search",
            "/api/v1/kb/review-queue",
            "/api/v1/kb/hierarchy/{node_type}/{node_id}",
            "/api/v1/generation/runs",
            "/api/v1/generation/runs/current",
            "/api/v1/generation/runs/{run_id}/artifacts",
        ):
            assert route in contract
        for forbidden in ("formal-freeze", "promote", "/publish", "tag-edit"):
            assert forbidden not in contract

    def test_teacher_token_and_principal_are_required(self):
        with tempfile.TemporaryDirectory() as temp, running_server(build_config(Path(temp))) as server:
            status, body = request(server, "GET", "/api/v1/kb/taxonomy", token=None)
            assert status == 401
            assert body["error"]["code"] == "authentication_required"
            status, body = request(server, "GET", "/api/v1/kb/taxonomy", token=STUDENT_TOKEN)
            assert status == 403
            assert body["error"]["code"] == "teacher_scope_required"

    def test_taxonomy_exposes_read_only_kacrd(self):
        with tempfile.TemporaryDirectory() as temp, running_server(build_config(Path(temp))) as server:
            status, body = request(server, "GET", "/api/v1/kb/taxonomy")
            assert status == 200
            data = body["data"]
            assert set(data["dimensions"]) == {"K", "A", "C", "R", "D"}
            assert all(data["dimensions"][key] for key in data["dimensions"])
            assert data["read_only"] is True
            assert data["edits_allowed"] is False
            assert len(data["sha256"]) == 64

    def test_a01_c01_candidates_are_searchable_without_human_upgrade(self):
        reader = PublicKBReader(SHCHEM_ROOT)
        for filter_name, axis, tag in (
            ("ability_A", "A", "A01"),
            ("context_C", "C", "C01"),
        ):
            result = reader.search(
                {
                    "query": "",
                    "purpose": "teacher_review",
                    "filters": {
                        "node_type": "atomic_part",
                        filter_name: tag,
                    },
                    "limit": 5,
                }
            )
            assert result["total"] > 0
            assert result["items"]
            for item in result["items"]:
                evidence = item["classification"]["tag_evidence"][axis]
                assert tag in item["classification"]["tags"][axis]
                assert tag in evidence["candidate_values"]
                assert evidence["evidence_state"] == "machine_candidate"
                assert evidence["human_verified"] is False
                assert evidence["candidate_only"] is True
                assert item["lifecycle"]["human_reviewed"] is False

    def test_private_paths_and_unsafe_filters_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp, running_server(build_config(Path(temp))) as server:
            for payload, expected in (
                ({"query": "06_学生错题档案", "purpose": "teacher_review", "filters": {}}, "private_scope_rejected"),
                ({"query": "", "purpose": "teacher_review", "filters": {"path": "private_profiles"}}, "unsafe_filter"),
                ({"query": "K10", "filters": {}}, "purpose_required"),
            ):
                status, body = request(server, "POST", "/api/v1/kb/search", payload=payload)
                assert status in {400, 403}
                assert body["error"]["code"] == expected
            status, body = request(server, "GET", "/api/v1/kb/hierarchy/atomic_part/%2e%2e")
            assert status == 400
            assert body["error"]["code"] == "invalid_identifier"

    def test_public_atomic_node_retains_all_parent_layers_and_no_pixels(self):
        with tempfile.TemporaryDirectory() as temp, running_server(build_config(Path(temp))) as server:
            status, found = request(
                server,
                "POST",
                "/api/v1/kb/search",
                payload={"query": "BS2026-YM-S4-Q1-P1", "purpose": "teacher_review", "filters": {"node_type": "atomic_part"}, "limit": 1},
            )
            assert status == 200
            node_id = found["data"]["items"][0]["node_id"]
            status, node = request(server, "GET", f"/api/v1/kb/hierarchy/atomic_part/{node_id}")
            assert status == 200
            assert node["data"]["hierarchy_path"] == ["paper", "theme_big_question", "printed_question", "atomic_part"]
            assert [item["node_type"] for item in node["data"]["parents"]] == ["paper", "theme_big_question", "printed_question"]
            assert node["data"]["lifecycle"]["release"] is False
            status, evidence = request(server, "GET", f"/api/v1/kb/hierarchy/atomic_part/{node_id}/evidence")
            assert status == 200
            assert evidence["data"]["content_exposed"] is False
            assert evidence["data"]["source_pixels_exposed"] is False
            assert "path" not in json.dumps(evidence["data"]["physical_provenance"])

    def test_all_135_printed_orphans_are_pending_not_root(self):
        reader = PublicKBReader(SHCHEM_ROOT)
        printed_rows = reader._load_layers()["printed_question"]
        orphan_ids = [
            row["printed_question_id"]
            for row in printed_rows
            if not row.get("parent_theme_big_question_id")
        ]
        assert len(orphan_ids) == 135
        for node_id in orphan_ids:
            node = reader.node("printed_question", node_id)
            assert node["parent_chain_status"] == "missing_parent_pending_review"
            assert node["parent_display"] == "父节点缺失 / unknown / 待复核"
            assert node["hierarchy_path_complete"] is False
            assert node["parent_chain_status"] != "paper_root"
            assert node["metadata"]["parent_binding_status"]

    def test_review_queue_is_manifest_verified_and_read_only(self):
        with tempfile.TemporaryDirectory() as temp, running_server(build_config(Path(temp))) as server:
            status, body = request(server, "GET", "/api/v1/kb/review-queue?limit=3")
            assert status == 200
            data = body["data"]
            assert data["count"] == 3
            assert data["read_only"] is True
            assert data["human_review_pending"] is True
            assert data["index_integrity"]["hash_verified_on_read"] is True

    def test_r18_run_artifacts_and_gate_timeline_remain_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp, running_server(build_config(Path(temp))) as server:
            status, body = request(server, "GET", "/api/v1/generation/runs/current")
            assert status == 200
            data = body["data"]
            assert data["run_id"] == "r18"
            assert data["task_card"]["paper_id"] == data["paper"]["paper_id"]
            # This test reads the live R18 fixture.  A concurrent append-only
            # rebuild may truthfully downgrade it; the invariant is that the
            # public flag exactly follows the integrity gate and never unlocks
            # release by itself.
            assert data["machine_pass"] is data["integrity"]["machine_pass_gate"]
            if data["machine_pass"]:
                assert data["integrity"]["controlled_path_set_exact"] is True
                assert data["integrity"]["all_controlled_files_live_verified"] is True
                assert data["integrity"]["required_qa_all_pass"] is True
            else:
                assert data["integrity"]["blockers"]
            assert data["artifact_count"] == len(
                GenerationRunReader.CONTROLLED_PATHS
            )
            assert data["human_review_pending"] is True
            assert data["release"] is False
            assert data["authority_changing_actions"] == []
            assert [item["stage"] for item in data["gate_timeline"]] == ["candidate", "machine_pass", "human_review_pending", "release"]
            assert data["gate_timeline"][-1]["status"] == "blocked"
            status, artifacts = request(server, "GET", "/api/v1/generation/runs/r18/artifacts")
            assert status == 200
            assert artifacts["data"]["items"]
            assert all(len(item["sha256"]) == 64 for item in artifacts["data"]["items"])
            assert artifacts["data"]["download_available"] is False

    def test_r18_integrity_mutations_always_downgrade_machine_pass(self):
        mutations = {
            "self_hash_bad": "double_clean_self_hash_invalid",
            "binding_missing": "controlled_file_allowlist_mismatch",
            "binding_duplicate": "controlled_file_allowlist_mismatch",
            "binding_extra": "controlled_file_allowlist_mismatch",
            "binding_bytes": "controlled_file_live_hash_verification_failed",
            "binding_hash": "controlled_file_live_hash_verification_failed",
            "live_drift": "controlled_file_live_hash_verification_failed",
            "qa_fail": "required_qa_not_all_pass",
        }
        for mutation, blocker in mutations.items():
            with tempfile.TemporaryDirectory() as temp:
                shchem_root = build_generation_fixture(Path(temp), mutation)
                result = GenerationRunReader(shchem_root).current()
                assert result["stage"] == "candidate", mutation
                assert result["machine_pass"] is False, mutation
                assert result["integrity"]["machine_pass_gate"] is False, mutation
                assert blocker in result["integrity"]["blockers"], mutation
                machine_timeline = next(
                    item
                    for item in result["gate_timeline"]
                    if item["stage"] == "machine_pass"
                )
                assert machine_timeline["status"] == "blocked", mutation

    def test_synthetic_exact_generation_fixture_reaches_machine_pass_only(self):
        with tempfile.TemporaryDirectory() as temp:
            shchem_root = build_generation_fixture(Path(temp))
            result = GenerationRunReader(shchem_root).current()
            assert result["stage"] == "machine_pass"
            assert result["machine_pass"] is True
            assert result["integrity"]["controlled_path_set_exact"] is True
            assert result["integrity"]["live_verified_count"] == len(
                GenerationRunReader.CONTROLLED_PATHS
            )
            assert result["release"] is False

    def test_dashboard_aggregates_gates_without_release_overclaim(self):
        with tempfile.TemporaryDirectory() as temp, running_server(build_config(Path(temp))) as server:
            status, body = request(server, "GET", "/api/v1/dashboard/status")
            assert status == 200
            data = body["data"]
            assert {
                "controller",
                "validation",
                "registry",
                "provider",
                "publication",
            }.issubset(data)
            assert data["publication"]["status"] == "blocked"
            assert data["publication"]["external_publication_allowed"] is False
            assert data["state_mutating_endpoints_present"] is True
            assert data["privilege_escalation_endpoints_present"] is False
            assert data["formal_freeze_endpoints_present"] is False
            assert data["register_endpoints_present"] is False
            assert data["promote_endpoints_present"] is False
            assert data["publish_endpoints_present"] is False
            assert data["tag_edit_endpoints_present"] is False
            assert "write_endpoints_present" not in data
            assert data["authority_changing_actions"] == []

    def test_student_principal_is_restricted_before_teacher_ui_connects(self):
        with tempfile.TemporaryDirectory() as temp, running_server(
            build_config(Path(temp))
        ) as server:
            status, body = request(
                server,
                "GET",
                "/api/v1/dashboard/status",
                token=STUDENT_TOKEN,
            )
            assert status == 403
            assert body["error"]["code"] == "teacher_scope_required"
        script = (OVERLAY / "app.js").read_text(encoding="utf-8")
        handshake = script.index('api("/api/v1/dashboard/status")')
        connected = script.index("setConnected(\n        true", handshake)
        assert handshake < connected
        assert '$("dashboardValidation").textContent = "校验中"' in script
        assert "dashboardPromise.then((result)" in script
        assert "const validationLive = value.validation.live_controller_output === true" in script
        assert ': "UNAVAILABLE";' in script
        assert "受限·需教师令牌" in script
        assert "principal_role=student" in script
        assert "setConnected(\n        false" in script

    def test_public_status_and_dashboard_remove_host_and_private_paths(self):
        safe_sha = "a" * 64
        leaked_status = {
            "ready": True,
            "observed_profile_path": str(WORKSPACE / "sh-chem-db/kb/profile.json"),
            "nested": {
                "safe": "candidate",
                "safe_code": "K/A/C/R/D provider_offline",
                "safe_http": "https://example.invalid/provider/status",
                "safe_api": "/api/v1/status",
                "safe_sha": safe_sha,
                "embedded_drive": r"provider observation: C:\Users\teacher\record.json",
                "leading_drive": "   D:\\service\\profile.json",
                "unc_value": r"provider observation: \\server\share\record.json",
                "forward_drive": "provider observation: C:/Users/teacher/record.json",
                "file_uri": "provider observation: file:///C:/Users/teacher/record.json",
                "encoded_drive": "provider=C%3A%5CUsers%5Cteacher%5Crecord.json",
                "encoded_unc": "provider=%5C%5Cserver%5Cshare%5Crecord.json",
                "encoded_file_uri": "provider=file%3A%2F%2F%2FC%3A%2FUsers%2Fteacher",
                "private_marker": "provider private_profiles record",
                r"C:\Users\teacher\leaked-key": "candidate",
            },
            "mixed_list": [
                "candidate",
                "trace from C%253A%255CUsers%255Cteacher%255Crecord.json",
                r"trace from C:\Users\teacher\record.json",
                "https://example.invalid/healthy",
            ],
        }

        def assert_public_projection(data):
            serialized = json.dumps(data, ensure_ascii=False).casefold()
            for marker in (
                r"c:\\users",
                str(WORKSPACE).casefold().replace("\\", "\\\\"),
                "private_state",
                "private_profiles",
                "06_学生错题档案",
            ):
                assert marker not in serialized
            assert "observed_profile_path" not in serialized
            assert data["public_projection"]["absolute_host_paths_removed"] is True

        def assert_nested_projection(data):
            nested = data["nested"]
            assert nested == {
                "safe": "candidate",
                "safe_code": "K/A/C/R/D provider_offline",
                "safe_http": "https://example.invalid/provider/status",
                "safe_api": "/api/v1/status",
                "safe_sha": safe_sha,
            }
            assert data["mixed_list"] == [
                "candidate",
                "https://example.invalid/healthy",
            ]

        with tempfile.TemporaryDirectory() as temp, running_server(
            build_config(Path(temp))
        ) as server:
            server.service.generation_bridge.status = lambda: leaked_status
            for token in (TEACHER_TOKEN, STUDENT_TOKEN):
                status, body = request(
                    server, "GET", "/api/v1/status", token=token
                )
                assert status == 200
                assert_public_projection(body["data"])
                assert body["data"]["generation_v2"]["nested"]["safe"] == "candidate"
                assert_nested_projection(body["data"]["generation_v2"])
            status, body = request(
                server,
                "GET",
                "/api/v1/dashboard/status",
                token=TEACHER_TOKEN,
            )
            assert status == 200
            assert_public_projection(body["data"])
            assert body["data"]["provider"]["generation"]["nested"]["safe"] == "candidate"
            assert_nested_projection(body["data"]["provider"]["generation"])

    def test_status_path_helper_decodes_and_scans_the_entire_string(self):
        unsafe = (
            r"provider observation: C:\Users\teacher\record.json",
            "   D:\\service\\profile.json",
            r"provider observation: \\server\share\record.json",
            "provider observation: C:/Users/teacher/record.json",
            "provider observation: file:///C:/Users/teacher/record.json",
            "provider=C%3A%5CUsers%5Cteacher%5Crecord.json",
            "provider=%5C%5Cserver%5Cshare%5Crecord.json",
            "provider=file%3A%2F%2F%2FC%3A%2FUsers%2Fteacher",
            "provider=C%253A%255CUsers%255Cteacher%255Crecord.json",
            "provider private_state record",
        )
        assert all(_status_sensitive_string(value, WORKSPACE) for value in unsafe)
        assert not any(
            _status_sensitive_string(value, WORKSPACE)
            for value in (
                "candidate",
                "K/A/C/R/D provider_offline",
                "https://example.invalid/provider/status",
                "http://127.0.0.1:8080/api/v1/status",
                "/api/v1/status",
                "a" * 64,
            )
        )
        assert _status_string_variants("C%253A%255CUsers")[-1] == r"C:\Users"

    def test_connect_resets_all_teacher_derived_dom_before_every_handshake(self):
        script = (OVERLAY / "app.js").read_text(encoding="utf-8")
        connect_start = script.index("async function connect()")
        next_function = script.index("function fileToBase64", connect_start)
        connect_source = script[connect_start:next_function]
        assert connect_source.index("clearTeacherDerivedState();") < connect_source.index(
            'api("/api/v1/status")'
        )
        assert connect_source.count("clearTeacherDerivedState();") >= 2
        assert connect_source.index("clearTeacherDerivedState();", 1) < connect_source.index(
            "setConnected(\n        false", connect_source.index("catch (error)")
        )
        reset_start = script.index("function clearTeacherDerivedState()")
        reset_end = script.index("function setRoute()", reset_start)
        reset_source = script[reset_start:reset_end]
        for required_id in (
            "runTaskCard",
            "runPaper",
            "runQa",
            "kbResults",
            "hierarchyDetail",
            "reviewQueue",
            "taxonomyGrid",
            "generationArtifacts",
            "diagnosisContributions",
            "childChains",
            "output",
            "errorAlert",
        ):
            assert f'$("{required_id}")' in reset_source or f'resetBlock("{required_id}"' in reset_source
        assert '$("token").value = ""' in reset_source
        assert "state.currentRun = null" in reset_source
        assert "state.taxonomy = null" in reset_source

    def test_responsive_css_has_real_shrink_and_long_token_guards(self):
        css = (OVERLAY / "styles.css").read_text(encoding="utf-8")
        compact = " ".join(css.split())
        assert "overflow-wrap: anywhere" in compact
        assert ".notice { min-width: 0; max-width: 100%" in compact
        assert "code { min-width: 0; max-width: 100%" in compact
        assert "input, select, button { min-width: 0; max-width: 100%" in compact
        assert "@media (max-width: 1080px)" in compact
        assert ".auth-card, .qa-row, .artifact-row { grid-template-columns: minmax(0, 1fr);" in compact
        assert "@media (max-width: 900px)" in compact

    def test_hash_tabs_and_accessible_dom_inventory(self):
        html = (OVERLAY / "index.html").read_text(encoding="utf-8")
        parser = MarkupInventory()
        parser.feed(html)
        assert len(parser.tabs) == 14
        assert len(parser.panels) == 14
        assert parser.inline_scripts == 0
        assert len(parser.live_regions) >= 8
        assert "errorAlert" in parser.alert_ids
        legacy_tabs = [item for item in parser.tabs if item.get("href")]
        detail_tabs = [item for item in parser.tabs if not item.get("href")]
        assert len(legacy_tabs) == 10
        assert len(detail_tabs) == 4
        assert {item["href"] for item in legacy_tabs} == {
            "#/home", "#/presentations", "#/overview", "#/generation",
            "#/retrieval", "#/kb", "#/candidate-review", "#/taxonomy",
            "#/students", "#/artifacts",
        }
        assert all(item.get("aria-controls") for item in parser.tabs)
        assert all(item.get("aria-labelledby") for item in parser.panels)
        for label in ("candidate 候选", "machine_pass 机器通过", "human_review_pending 待人审", "release 发布关闭"):
            assert label in html
        for forbidden in ("正式冻结", "注册并发布", "提升为正式", "编辑标签"):
            assert f">{forbidden}<" not in html
