from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from integrations.deeptutor_shchem_v1.config import Principal, token_digest
from integrations.deeptutor_shchem_v1.public_kb import PublicKBReader
from integrations.deeptutor_shchem_v1.tagging_workbench import TagPatchGateway
from integrations.shchem_tagging_workbench_v1 import AppendOnlyTagPatchStore
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    OVERLAY,
    build_config,
    request,
    running_server,
)

TEACHER_WRITE_TOKEN = "tag-patch-teacher-write-0123456789"
TEACHER_READ_TOKEN = "tag-patch-teacher-read-0123456789"
STUDENT_TOKEN = "tag-patch-student-0123456789"
PRODUCTION_STORE = (
    Path(__file__).resolve().parents[4]
    / "staging/coordination/tagging_workbench/candidate_store_v1"
)
INDEX_ROOT = "kb/classification/theme_hierarchy_master_index_v1_2026-08-03"


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _jsonl_bytes(rows) -> bytes:
    return b"".join(
        (
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        for row in rows
    )


def build_public_fixture(workspace: Path) -> Path:
    root = workspace / "public-fixture"
    payloads = {
        f"{INDEX_ROOT}/paper_records.jsonl": _jsonl_bytes(
            [
                {
                    "paper_id": "PAPER-001",
                    "record_type": "paper",
                    "paper_title": "Synthetic public candidate",
                    "gates": {"human_reviewed": False},
                }
            ]
        ),
        f"{INDEX_ROOT}/theme_big_question_records.jsonl": _jsonl_bytes(
            [
                {
                    "theme_big_question_id": "THEME-001",
                    "parent_paper_id": "PAPER-001",
                    "record_type": "theme_big_question",
                    "gates": {"human_reviewed": False},
                }
            ]
        ),
        f"{INDEX_ROOT}/printed_question_records.jsonl": _jsonl_bytes(
            [
                {
                    "printed_question_id": "PRINTED-001",
                    "parent_theme_big_question_id": "THEME-001",
                    "record_type": "printed_question",
                    "gates": {"human_reviewed": False},
                }
            ]
        ),
        f"{INDEX_ROOT}/atomic_part_records.jsonl": _jsonl_bytes(
            [
                {
                    "atomic_part_id": "ATOM-001",
                    "parent_paper_id": "PAPER-001",
                    "parent_printed_question_id": "PRINTED-001",
                    "parent_theme_big_question_id": "THEME-001",
                    "record_type": "atomic_part",
                    "item_type": {
                        "value": "short_fill",
                        "candidate_values": ["short_fill"],
                    },
                    "selection_rule": "not_applicable",
                    "primary_knowledge_K": {"candidate_values": ["K01"]},
                    "supporting_knowledge_K": {"candidate_values": []},
                    "ability_A": {"candidate_values": ["A01"]},
                    "context_C": {"candidate_values": ["C01"]},
                    "response_R": ["R02"],
                    "representation_RP": ["RP01"],
                    "difficulty": {"declared_prelabel": "D1"},
                    "gates": {
                        "human_reviewed": False,
                        "retrieval_ready": False,
                        "generation_allowed": False,
                        "publication_allowed": False,
                    },
                },
                {
                    "atomic_part_id": "ATOM-002",
                    "parent_paper_id": "PAPER-001",
                    "parent_printed_question_id": "PRINTED-001",
                    "parent_theme_big_question_id": "THEME-001",
                    "record_type": "atomic_part",
                    "item_type": {"value": "reasoned_explanation"},
                    "selection_rule": "not_applicable",
                    "primary_knowledge_K": {"candidate_values": ["K01"]},
                    "supporting_knowledge_K": {"candidate_values": []},
                    "ability_A": {"candidate_values": ["A01"]},
                    "context_C": {"candidate_values": ["C01"]},
                    "response_R": ["R02"],
                    "representation_RP": ["RP01"],
                    "difficulty": {"declared_prelabel": "D1"},
                    "gates": {"human_reviewed": False},
                },
            ]
        ),
        f"{INDEX_ROOT}/human_review_queue.jsonl": b"",
        "kb/knowledge_taxonomy.json": _json_bytes(
            {
                "schema_version": "1.0.0",
                "title": "Synthetic controlled taxonomy",
                "scope": "test-only",
                "evidence_note": "synthetic fixture",
                "dimensions": {
                    "knowledge_points": [
                        {"id": "K01", "name": "K one"},
                        {"id": "K02", "name": "K two"},
                    ],
                    "abilities": [
                        {"id": "A01", "name": "A one"},
                        {"id": "A02", "name": "A two"},
                    ],
                    "contexts": [{"id": "C01", "name": "C one"}],
                    "response_types": [
                        {"id": "R01", "name": "R one"},
                        {"id": "R02", "name": "R two"},
                    ],
                    "difficulty": [
                        {"id": "D1", "name": "D one"},
                        {"id": "D2", "name": "D two"},
                    ],
                },
            }
        ),
    }
    for relative, raw in payloads.items():
        path = root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    bindings = [
        {"path": relative, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        for relative, raw in payloads.items()
        if relative.startswith(f"{INDEX_ROOT}/")
    ]
    manifest = {
        "status": "machine_pass_synthetic_fixture",
        "claim_boundary": "synthetic structural fixture only",
        "output_bindings": bindings,
    }
    manifest_path = root.joinpath(*f"{INDEX_ROOT}/manifest.json".split("/"))
    manifest_path.write_bytes(_json_bytes(manifest))
    return root


def build_tag_config(temp_root: Path, public_root: Path):
    config = build_config(temp_root / "gateway-state")
    config.shchem_root = public_root
    config.principals = [
        Principal(
            "tag-teacher-write",
            "teacher",
            token_digest(TEACHER_WRITE_TOKEN),
            (),
            ("tag_patch_candidate_write",),
        ),
        Principal(
            "tag-teacher-read",
            "teacher",
            token_digest(TEACHER_READ_TOKEN),
        ),
        Principal(
            "tag-student",
            "student",
            token_digest(STUDENT_TOKEN),
        ),
    ]
    config.students = ()
    config.validate()
    return config


def origin(server) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"


def install_test_adapter(server, temp_root: Path, public_root: Path):
    store_workspace = temp_root / "tag-store-workspace"
    store_workspace.mkdir()
    store = AppendOnlyTagPatchStore.for_test_workspace(store_workspace)
    adapter = TagPatchGateway(
        PublicKBReader(public_root),
        temp_root,
        store=store,
    )
    server.service.public_kb = adapter.public_kb
    server.service.tag_patch_workbench = adapter
    return adapter, store


def current_context(server, node_id="ATOM-001", token=TEACHER_WRITE_TOKEN):
    status, body, _ = request(
        server,
        "GET",
        f"/api/v1/kb/hierarchy/atomic_part/{node_id}",
        token=token,
    )
    assert status == 200, body
    return body["data"]["tag_patch_candidate_context"]


def candidate_payload(context, *, node_id="ATOM-001", reason="Teacher correction"):
    return {
        "node_type": "atomic_part",
        "node_id": node_id,
        "factor_updates": {
            "primary_knowledge_K": "K02",
            "ability_A": ["A02"],
            "response_R": "R02",
            "representation_RP": ["RP02"],
        },
        "reason": reason,
        "expected_current_public_node_identity": context[
            "expected_current_public_node_identity"
        ],
    }


def assert_candidate_only(value):
    assert value["candidate_only"] is True
    assert value["human_reviewed"] is False
    assert value["retrieval_ready"] is False
    assert value["generation_allowed"] is False
    assert value["publication_allowed"] is False
    assert value["official"] is False
    assert value["teaching_use_allowed"] is False


def assert_no_host_or_private_paths(value):
    serialized = json.dumps(value, ensure_ascii=False).casefold()
    for marker in (
        "c:\\\\users",
        "private_state",
        "private_profiles",
        "06_学生错题档案",
        "state_root",
        "workspace_root",
        "relative_path",
        "file://",
    ):
        assert marker not in serialized


def test_teacher_write_capability_create_list_get_and_diff_are_candidate_only():
    assert not PRODUCTION_STORE.exists()
    with tempfile.TemporaryDirectory() as temp:
        temp_root = Path(temp)
        public_root = build_public_fixture(temp_root)
        with running_server(build_tag_config(temp_root, public_root)) as server:
            _adapter, store = install_test_adapter(server, temp_root, public_root)
            context = current_context(server)
            assert context["patchable"] is True
            assert_candidate_only(context)

            route = "/api/v1/tagging/workbench/tag-patches/candidates"
            status, empty, _ = request(
                server, "GET", route, token=TEACHER_READ_TOKEN
            )
            assert status == 200
            assert empty["data"]["count"] == 0

            status, created, _ = request(
                server,
                "POST",
                route,
                token=TEACHER_WRITE_TOKEN,
                payload=candidate_payload(
                    context,
                    reason=r"Teacher saw C:\Users\private\note.txt",
                ),
                headers={"Origin": origin(server), "Sec-Fetch-Site": "same-origin"},
            )
            assert status == 201, created
            patch = created["data"]
            assert patch["candidate_status"] == "pending_human_review_not_applied"
            assert_candidate_only(patch)
            assert patch["actions"] == {
                "apply_available": False,
                "human_approve_available": False,
                "retrieval_activation_available": False,
                "generation_activation_available": False,
                "publication_available": False,
                "official_claim_available": False,
            }
            assert {row["field"] for row in patch["diff"]} == {
                "primary_knowledge_K",
                "ability_A",
                "response_R",
                "representation_RP",
            }
            assert patch["reason"] == "[redacted_host_or_private_text]"
            assert_no_host_or_private_paths(patch)
            committed = store.get_patch(patch["patch_id"])
            assert committed["record"]["reason"] == "[redacted_host_or_private_text]"

            status, listed, _ = request(
                server,
                "GET",
                f"{route}?node_id=ATOM-001&limit=20&offset=0",
                token=TEACHER_READ_TOKEN,
            )
            assert status == 200
            assert listed["data"]["count"] == 1
            assert_candidate_only(listed["data"])
            patch_id = patch["patch_id"]
            status, detail, _ = request(
                server,
                "GET",
                f"{route}/{patch_id}",
                token=TEACHER_READ_TOKEN,
            )
            assert status == 200
            assert detail["data"]["patch_id"] == patch_id
            assert detail["data"]["binding"]["current_public_identity_matches"] is True
            assert_no_host_or_private_paths(detail["data"])
            assert len(store.list_patches()) == 1
    assert not PRODUCTION_STORE.exists()


def test_auth_capability_origin_strict_body_and_stale_identity_fail_without_patch():
    with tempfile.TemporaryDirectory() as temp:
        temp_root = Path(temp)
        public_root = build_public_fixture(temp_root)
        with running_server(build_tag_config(temp_root, public_root)) as server:
            _adapter, store = install_test_adapter(server, temp_root, public_root)
            context = current_context(server)
            payload = candidate_payload(context)
            route = "/api/v1/tagging/workbench/tag-patches/candidates"
            trusted = {"Origin": origin(server), "Sec-Fetch-Site": "same-origin"}

            for method, token, body, headers, expected, code in (
                ("GET", None, None, None, 401, "authentication_required"),
                ("GET", STUDENT_TOKEN, None, None, 403, "teacher_scope_required"),
                (
                    "POST",
                    STUDENT_TOKEN,
                    payload,
                    trusted,
                    403,
                    "teacher_scope_required",
                ),
                (
                    "POST",
                    TEACHER_READ_TOKEN,
                    payload,
                    trusted,
                    403,
                    "tag_patch_write_capability_required",
                ),
                (
                    "POST",
                    TEACHER_WRITE_TOKEN,
                    payload,
                    None,
                    403,
                    "csrf_origin_required",
                ),
                (
                    "POST",
                    TEACHER_WRITE_TOKEN,
                    payload,
                    {"Origin": "https://evil.example"},
                    403,
                    "origin_denied",
                ),
            ):
                status, response, _ = request(
                    server,
                    method,
                    route,
                    token=token,
                    payload=body,
                    headers=headers,
                )
                assert status == expected
                assert response["error"]["code"] == code

            for forbidden in ("path", "state_root", "authority", "official"):
                injected = dict(payload)
                injected[forbidden] = "forbidden"
                status, response, _ = request(
                    server,
                    "POST",
                    route,
                    token=TEACHER_WRITE_TOKEN,
                    payload=injected,
                    headers=trusted,
                )
                assert status == 400
                assert response["error"]["code"] == "invalid_tag_patch_contract"

            smuggled = candidate_payload(context)
            smuggled["factor_updates"] = {"authority": {"official": True}}
            status, response, _ = request(
                server,
                "POST",
                route,
                token=TEACHER_WRITE_TOKEN,
                payload=smuggled,
                headers=trusted,
            )
            assert status == 400
            assert response["error"]["code"] == "invalid_tag_patch_contract"

            stale = candidate_payload(context)
            stale["expected_current_public_node_identity"] = dict(
                stale["expected_current_public_node_identity"]
            )
            stale["expected_current_public_node_identity"]["public_record_sha256"] = (
                "0" * 64
            )
            status, response, _ = request(
                server,
                "POST",
                route,
                token=TEACHER_WRITE_TOKEN,
                payload=stale,
                headers=trusted,
            )
            assert status == 409
            assert response["error"]["code"] == "stale_public_node_identity"
            assert store.list_patches() == []


def test_no_overwrite_and_injected_transaction_failure_leave_no_uncommitted_patch():
    with tempfile.TemporaryDirectory() as temp:
        temp_root = Path(temp)
        public_root = build_public_fixture(temp_root)
        with running_server(build_tag_config(temp_root, public_root)) as server:
            _adapter, store = install_test_adapter(server, temp_root, public_root)
            route = "/api/v1/tagging/workbench/tag-patches/candidates"
            trusted = {"Origin": origin(server), "Sec-Fetch-Site": "same-origin"}
            first_payload = candidate_payload(current_context(server))
            status, first, _ = request(
                server,
                "POST",
                route,
                token=TEACHER_WRITE_TOKEN,
                payload=first_payload,
                headers=trusted,
            )
            assert status == 201
            status, duplicate, _ = request(
                server,
                "POST",
                route,
                token=TEACHER_WRITE_TOKEN,
                payload=first_payload,
                headers=trusted,
            )
            assert status == 409
            assert duplicate["error"]["code"] == "tag_patch_conflict"
            assert len(store.list_patches()) == 1

            second_context = current_context(server, "ATOM-002")
            second_payload = candidate_payload(
                second_context,
                node_id="ATOM-002",
                reason="Injected rollback test",
            )
            original_write = store._write_exclusive
            calls = 0

            def fail_after_first_write(relative, data):
                nonlocal calls
                calls += 1
                observation = original_write(relative, data)
                if calls == 2:
                    raise OSError("injected tag-patch transaction failure")
                return observation

            store._write_exclusive = fail_after_first_write
            status, failed, _ = request(
                server,
                "POST",
                route,
                token=TEACHER_WRITE_TOKEN,
                payload=second_payload,
                headers=trusted,
            )
            assert status == 500
            assert failed["error"]["code"] == "internal_error"
            store._write_exclusive = original_write
            assert [row["patch_id"] for row in store.list_patches()] == [
                first["data"]["patch_id"]
            ]
            patch_dirs = sorted(path.name for path in (store.state_root / "patches").iterdir())
            assert patch_dirs == [first["data"]["patch_id"]]


def test_apply_approve_retrieval_generation_publication_and_official_routes_absent():
    with tempfile.TemporaryDirectory() as temp:
        temp_root = Path(temp)
        public_root = build_public_fixture(temp_root)
        with running_server(build_tag_config(temp_root, public_root)) as server:
            install_test_adapter(server, temp_root, public_root)
            base = "/api/v1/tagging/workbench/tag-patches/candidates/TAGPATCH-missing"
            for suffix in (
                "apply",
                "human-approve",
                "retrieval",
                "generation",
                "publication",
                "official",
            ):
                status, body, _ = request(
                    server,
                    "POST",
                    f"{base}/{suffix}",
                    token=TEACHER_WRITE_TOKEN,
                    payload={},
                    headers={"Origin": origin(server)},
                )
                assert status == 404
                assert body["error"]["code"] == "route_not_found"


def test_ui_contract_has_unapplied_review_state_diff_filters_and_role_reset():
    html = (OVERLAY / "index.html").read_text(encoding="utf-8")
    script = (OVERLAY / "app.js").read_text(encoding="utf-8")
    css = (OVERLAY / "styles.css").read_text(encoding="utf-8")
    for marker in (
        "候选标签补丁",
        "待人工复核",
        "未应用",
        "teaching_use_allowed",
        'id="tagPatchForm"',
        'id="tagPatchFilter"',
        'id="tagPatchList"',
        'id="tagPatchDiff"',
        'id="tagPatchApply"',
        'id="tagPatchHumanApprove"',
        'id="tagPatchActivate"',
        'id="tagPatchPublish"',
    ):
        assert marker in html
    for disabled_id in (
        "tagPatchApply",
        "tagPatchHumanApprove",
        "tagPatchActivate",
        "tagPatchPublish",
    ):
        assert f'id="{disabled_id}"' in html
        element = html.split(f'id="{disabled_id}"', 1)[1].split(">", 1)[0]
        assert "disabled" in element
        assert f'$("{disabled_id}").addEventListener' not in script

    start = script.index("function buildTagPatchRequest()")
    end = script.index("function renderTagPatchDetail", start)
    request_source = script[start:end]
    for allowed in (
        "node_type",
        "node_id",
        "factor_updates",
        "reason",
        "expected_current_public_node_identity",
    ):
        assert allowed in request_source
    for forbidden in (
        "path:",
        "state_root",
        "authority:",
        "human_reviewed:",
        "official:",
    ):
        assert forbidden not in request_source

    reset_start = script.index("function clearTeacherDerivedState()")
    reset_end = script.index("function setRoute()", reset_start)
    reset_source = script[reset_start:reset_end]
    for marker in (
        "state.tagPatchNode = null",
        "state.tagPatchWriteAllowed = false",
        'resetBlock("tagPatchList"',
        'resetBlock("tagPatchDiff"',
        '$("tagPatchReason").value = ""',
    ):
        assert marker in reset_source

    for viewport in (375, 768, 1024):
        assert viewport > 0
        assert "minmax(0, 1fr)" in css
        assert "overflow-wrap: anywhere" in css
        assert "html, body { width: 100%; max-width: 100%" in css
    assert ".tag-patch-grid" in css
    assert "@media (max-width: 760px)" in css


def test_openapi_and_overlay_manifest_keep_candidate_authority_closed():
    contract = (
        Path(__file__).resolve().parents[1] / "contracts/gateway_openapi_v1.yaml"
    ).read_text(encoding="utf-8")
    for route in (
        "/api/v1/tagging/workbench/tag-patches/candidates:",
        "/api/v1/tagging/workbench/tag-patches/candidates/{patch_id}:",
    ):
        assert route in contract
    assert "TagPatchBrowserRequest" in contract
    assert "expected_current_public_node_identity" in contract
    for forbidden in (
        "/tag-patches/{patch_id}/apply",
        "/tag-patches/{patch_id}/human-approve",
        "/tag-patches/{patch_id}/retrieval",
        "/tag-patches/{patch_id}/generation",
        "/tag-patches/{patch_id}/publication",
        "/tag-patches/{patch_id}/official",
    ):
        assert forbidden not in contract

    manifest = json.loads((OVERLAY / "overlay.manifest.json").read_text(encoding="utf-8"))
    slice2 = manifest["slice2_tag_patch"]
    assert slice2["create_list_get_only"] is True
    assert slice2["teacher_only"] is True
    assert slice2["write_capability_required"] == "tag_patch_candidate_write"
    assert slice2["apply_present"] is False
    assert slice2["human_approve_present"] is False
    assert_candidate_only(slice2["authority"])
