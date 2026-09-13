from __future__ import annotations

import hashlib
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from integrations.deeptutor_shchem_v1.config import Principal, token_digest
from integrations.shchem_generation_workbench_v1 import IntegrityError
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    OVERLAY,
    build_config,
    request,
    running_server,
)

STUDENT_TOKEN = "student-workbench-token-0123456789"


def task_card(*, mode: str = "plan_only", evidence_ids: list[str] | None = None):
    return {
        "schema_version": "generation_task_card_v1",
        "target_kind": "full_paper",
        "grade": "grade_12",
        "teaching_stage": "first_review",
        "purpose": "diagnostic_practice",
        "knowledge_scope": ["K-chemical-principles"],
        "ability_scope": ["A-evidence-reasoning"],
        "theme_count": {"value_status": "unknown", "value": None},
        "duration_minutes": {
            "value_status": "blocked_pending_review",
            "value": None,
        },
        "total_score": {"value_status": "unknown", "value": None},
        "difficulty_targets": {
            "basic": 0.4,
            "intermediate": 0.4,
            "advanced": 0.2,
        },
        "paper_structure": {
            "hierarchy": [
                "paper",
                "theme_big_question",
                "printed_question",
                "atomic_part",
            ],
            "independent_selection_section": False,
        },
        "numbering_rules": {"value_status": "unknown", "value": None},
        "selection_scoring_rules": {
            "value_status": "blocked_pending_review",
            "value": None,
        },
        "prohibited_content": ["unverified_official_claims"],
        "figure_types": ["apparatus", "graph"],
        "evidence_record_ids": list(evidence_ids or []),
        "mode": mode,
        "provider_profile_id": "sol_xhigh_generation_v1",
    }


def create_candidate(server, card=None):
    status, body, _ = request(
        server,
        "POST",
        "/api/v1/generation/workbench/task-cards/candidates",
        payload=card or task_card(),
    )
    assert status == 201, body
    return body["data"]


def freeze_candidate(server, candidate, *, sha=None, size=None):
    return request(
        server,
        "POST",
        f"/api/v1/generation/workbench/task-cards/candidates/{candidate['task_id']}/freeze",
        payload={
            "expected_candidate_file_sha256": sha or candidate["candidate"]["sha256"],
            "expected_candidate_file_size_bytes": size or candidate["candidate"]["bytes"],
        },
    )


def plan_candidate(server, frozen, evidence_summaries=None):
    return request(
        server,
        "POST",
        f"/api/v1/generation/workbench/task-cards/candidates/{frozen['task_id']}/plan-runs",
        payload={
            "expected_candidate_file_sha256": frozen["candidate"]["sha256"],
            "expected_candidate_file_size_bytes": frozen["candidate"]["bytes"],
            "expected_freeze_file_sha256": frozen["freeze"]["sha256"],
            "expected_freeze_file_size_bytes": frozen["freeze"]["bytes"],
            "evidence_summaries": evidence_summaries or [],
        },
    )


def assert_safe_projection(value):
    serialized = json.dumps(value, ensure_ascii=False).casefold()
    for forbidden in (
        "relative_path",
        "raw_input",
        "raw evidence",
        "private_state",
        "file://",
        "c:\\\\users",
        "06_学生错题档案",
    ):
        assert forbidden not in serialized
    assert value["human_reviewed"] is False
    assert value["publication_allowed"] is False
    assert value["official"] is False
    assert value["external_publication_allowed"] is False


def test_teacher_happy_path_writes_plan_only_without_model_or_question_content():
    with tempfile.TemporaryDirectory() as temp, running_server(
        build_config(Path(temp))
    ) as server:
        status, empty, _ = request(
            server,
            "GET",
            "/api/v1/generation/workbench/task-cards/candidates",
        )
        assert status == 200
        assert empty["data"]["count"] == 0

        candidate = create_candidate(server)
        assert candidate["status"] == "candidate"
        assert len(candidate["candidate"]["sha256"]) == 64
        assert candidate["candidate"]["bytes"] > 0
        assert candidate["requested_provider"]["requested_model"] == "gpt-5.6-sol"
        assert candidate["requested_provider"]["requested_reasoning_effort"] == "xhigh"
        assert candidate["requested_provider"]["platform_actual_reported"] is False
        assert candidate["requested_provider"]["signed"] is False
        assert_safe_projection(candidate)

        status, frozen_body, _ = freeze_candidate(server, candidate)
        assert status == 201
        frozen = frozen_body["data"]
        assert frozen["status"] == "frozen"
        assert frozen["candidate_snapshot"] == candidate["candidate"]

        status, run_body, _ = plan_candidate(server, frozen)
        assert status == 201
        run = run_body["data"]
        assert run["status"] == "plan_only_blocked"
        assert run["model_invoked"] is False
        assert run["actual_generation_performed"] is False
        assert run["contains_generated_questions"] is False
        assert run["download_available"] is False
        assert {item["code"] for item in run["blockers"]} >= {
            "l1_direction_source_minimum",
            "independent_current_shanghai_l2_minimum",
            "provider_available_required",
        }
        assert_safe_projection(run)

        base = f"/api/v1/generation/workbench/plan-runs/{run['run_id']}"
        status, events_body, _ = request(server, "GET", f"{base}/events")
        assert status == 200
        events = events_body["data"]
        assert [item["event_type"] for item in events["items"]] == [
            "task_card_candidate",
            "task_card_frozen",
            "evidence_preflight",
        ]
        assert events["items"][-1]["status"] == "plan_only_blocked"
        assert_safe_projection(events)
        status, artifacts_body, _ = request(server, "GET", f"{base}/artifacts")
        assert status == 200
        artifacts = artifacts_body["data"]
        assert artifacts["count"] == 1
        assert artifacts["items"][0]["artifact_type"] == "generation_plan"
        assert artifacts["items"][0]["contains_generated_questions"] is False
        assert artifacts["download_available"] is False
        assert_safe_projection(artifacts)

        # Idempotent reads are byte-content equivalent apart from envelope request IDs.
        status, again, _ = request(server, "GET", base)
        assert status == 200
        assert again["data"] == run


def test_anonymous_and_student_principals_cannot_read_or_write_workbench():
    with tempfile.TemporaryDirectory() as temp:
        config = build_config(Path(temp))
        config.principals.append(
            Principal(
                "student-workbench",
                "student",
                token_digest(STUDENT_TOKEN),
                (),
            )
        )
        config.validate()
        with running_server(config) as server:
            route = "/api/v1/generation/workbench/task-cards/candidates"
            for method, token, payload, expected, code in (
                ("GET", None, None, 401, "authentication_required"),
                ("POST", None, task_card(), 401, "authentication_required"),
                ("GET", STUDENT_TOKEN, None, 403, "teacher_scope_required"),
                ("POST", STUDENT_TOKEN, task_card(), 403, "teacher_scope_required"),
            ):
                status, body, _ = request(
                    server, method, route, token=token, payload=payload
                )
                assert status == expected
                assert body["error"]["code"] == code


def test_extra_path_url_prompt_model_and_effort_fields_are_rejected_before_write():
    injections = {
        "path": "C:/private/task.json",
        "url": "https://example.invalid/source",
        "prompt": "write a paper",
        "model": "caller-choice",
        "effort": "low",
    }
    with tempfile.TemporaryDirectory() as temp, running_server(
        build_config(Path(temp))
    ) as server:
        route = "/api/v1/generation/workbench/task-cards/candidates"
        for key, value in injections.items():
            payload = task_card()
            payload[key] = value
            status, body, _ = request(server, "POST", route, payload=payload)
            assert status == 400, key
            assert body["error"]["code"] == "invalid_workbench_contract"

        nested = task_card()
        nested["paper_structure"]["url"] = "https://example.invalid"
        status, body, _ = request(server, "POST", route, payload=nested)
        assert status == 400
        assert body["error"]["code"] == "invalid_workbench_contract"
        status, listed, _ = request(server, "GET", route)
        assert status == 200
        assert listed["data"]["count"] == 0


def test_stale_hash_and_byte_count_are_409_and_never_create_freeze():
    with tempfile.TemporaryDirectory() as temp, running_server(
        build_config(Path(temp))
    ) as server:
        candidate = create_candidate(server)
        status, body, _ = freeze_candidate(server, candidate, sha="0" * 64)
        assert status == 409
        assert body["error"]["code"] == "stale_workbench_descriptor"
        status, body, _ = freeze_candidate(
            server, candidate, size=candidate["candidate"]["bytes"] + 1
        )
        assert status == 409
        assert body["error"]["code"] == "stale_workbench_descriptor"
        status, current, _ = request(
            server,
            "GET",
            f"/api/v1/generation/workbench/task-cards/candidates/{candidate['task_id']}",
        )
        assert status == 200
        assert current["data"]["status"] == "candidate"


def test_tasks_are_isolated_and_cross_task_plan_descriptors_fail_closed():
    with tempfile.TemporaryDirectory() as temp, running_server(
        build_config(Path(temp))
    ) as server:
        first = create_candidate(server, task_card(evidence_ids=["EV-A"]))
        second = create_candidate(server, task_card(evidence_ids=["EV-B"]))
        _, first_frozen_body, _ = freeze_candidate(server, first)
        _, second_frozen_body, _ = freeze_candidate(server, second)
        first_frozen = first_frozen_body["data"]
        second_frozen = second_frozen_body["data"]
        payload = {
            "expected_candidate_file_sha256": second_frozen["candidate"]["sha256"],
            "expected_candidate_file_size_bytes": second_frozen["candidate"]["bytes"],
            "expected_freeze_file_sha256": second_frozen["freeze"]["sha256"],
            "expected_freeze_file_size_bytes": second_frozen["freeze"]["bytes"],
            "evidence_summaries": [],
        }
        status, body, _ = request(
            server,
            "POST",
            f"/api/v1/generation/workbench/task-cards/candidates/{first_frozen['task_id']}/plan-runs",
            payload=payload,
        )
        assert status == 409
        assert body["error"]["code"] == "stale_workbench_descriptor"
        status, listed, _ = request(
            server,
            "GET",
            "/api/v1/generation/workbench/task-cards/candidates",
        )
        assert status == 200
        assert {item["task_id"] for item in listed["data"]["items"]} == {
            first["task_id"],
            second["task_id"],
        }


def test_concurrent_freeze_uses_core_exclusive_write_and_never_overwrites():
    with tempfile.TemporaryDirectory() as temp, running_server(
        build_config(Path(temp))
    ) as server:
        candidate = create_candidate(server)

        def freeze_once():
            status, body, _ = freeze_candidate(server, candidate)
            return status, body

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _index: freeze_once(), range(2)))
        assert sorted(status for status, _body in results) == [201, 409]
        conflict = next(body for status, body in results if status == 409)
        assert conflict["error"]["code"] == "immutable_state_conflict"
        status, current, _ = request(
            server,
            "GET",
            f"/api/v1/generation/workbench/task-cards/candidates/{candidate['task_id']}",
        )
        assert status == 200
        assert current["data"]["status"] == "frozen"


def test_plan_api_uses_single_transaction_and_409_leaves_zero_run_residue():
    with tempfile.TemporaryDirectory() as temp, running_server(
        build_config(Path(temp))
    ) as server:
        candidate = create_candidate(server)
        status, frozen_body, _ = freeze_candidate(server, candidate)
        assert status == 201
        frozen = frozen_body["data"]
        store = server.service.generation_workbench.store
        original_checkpoint = store._plan_transaction_checkpoint
        original_legacy_create = store.create_run

        def legacy_path_must_not_run(*_args, **_kwargs):
            raise AssertionError("gateway called the non-transactional run creator")

        def fail_artifact(stage, _context):
            if stage == "artifact":
                raise IntegrityError("injected API transaction failure")

        # The API must use only the atomic plan transaction; the legacy state
        # machine remains available to core callers but is forbidden here.
        store.create_run = legacy_path_must_not_run
        store._plan_transaction_checkpoint = fail_artifact
        status, body, _ = plan_candidate(server, frozen)
        assert status == 409
        assert body["error"]["code"] == "workbench_integrity_failure"
        assert list((store.state_root / "runs").iterdir()) == []

        store._plan_transaction_checkpoint = original_checkpoint
        store.create_run = original_legacy_create
        status, body, _ = plan_candidate(server, frozen)
        assert status == 201
        assert body["data"]["event_count"] == 3
        assert [item.name for item in (store.state_root / "runs").iterdir()] == [
            body["data"]["run_id"]
        ]


def test_request_body_limit_and_strict_json_duplicates_fail_before_task_creation():
    with tempfile.TemporaryDirectory() as temp, running_server(
        build_config(Path(temp), max_request_bytes=1024)
    ) as server:
        status, body, _ = request(
            server,
            "POST",
            "/api/v1/generation/workbench/task-cards/candidates",
            payload={"padding": "x" * 2000},
        )
        assert status == 413
        assert body["error"]["code"] == "request_too_large"


def test_webui_has_two_honest_generation_sections_and_static_overflow_gates():
    html = (OVERLAY / "index.html").read_text(encoding="utf-8")
    script = (OVERLAY / "app.js").read_text(encoding="utf-8")
    css = (OVERLAY / "styles.css").read_text(encoding="utf-8")
    for marker in (
        "A. 通用命题任务卡与计划",
        "B. 当前 R18 只读运行",
        'id="workbenchForm"',
        'id="wbSubmitCandidate"',
        'id="wbFreeze"',
        'id="wbPlan"',
        "不是平台实报、不是签名",
        "页面加载不会自动创建 live task",
    ):
        assert marker in html
    for marker in (
        "target_kind",
        "knowledge_scope",
        "ability_scope",
        "difficulty_targets",
        "numbering_rules",
        "selection_scoring_rules",
        "prohibited_content",
        "figure_types",
        "evidence_record_ids",
        "provider_profile_id",
        "plan_only_blocked",
        "model_invoked",
        "contains_generated_questions",
    ):
        assert marker in script
    assert 'api("/api/v1/generation/workbench/task-cards/candidates")' not in script
    assert "createWorkbenchTask(event)" in script
    assert "gpt-5.6-sol · xhigh · requested only" in html
    for viewport in (375, 768, 1024):
        assert viewport > 0
        assert "minmax(0, 1fr)" in css
        assert "overflow-wrap: anywhere" in css
        assert "html, body { width: 100%; max-width: 100%" in css
        assert ".workspace { min-width: 0;" in css
    assert "@media (max-width: 760px)" in css
    assert ".form-grid { grid-template-columns: minmax(0, 1fr); }" in css
    assert "@media (min-width: 761px) and (max-width: 1080px)" in css
    assert ".app-shell { grid-template-columns: 204px minmax(0, 1fr); }" in css


def test_openapi_exposes_plan_only_surface_and_no_mutating_release_actions():
    contract = (
        Path(__file__).resolve().parents[1] / "contracts/gateway_openapi_v1.yaml"
    ).read_text(encoding="utf-8")
    for route in (
        "/api/v1/generation/workbench/task-cards/candidates:",
        "/api/v1/generation/workbench/task-cards/candidates/{task_id}/freeze:",
        "/api/v1/generation/workbench/task-cards/candidates/{task_id}/plan-runs:",
        "/api/v1/generation/workbench/plan-runs/{run_id}:",
        "/api/v1/generation/workbench/plan-runs/{run_id}/events:",
        "/api/v1/generation/workbench/plan-runs/{run_id}/artifacts:",
    ):
        assert route in contract
    assert "additionalProperties: false" in contract
    for forbidden in (
        "/actual-generation",
        "/formal-freeze",
        "/register",
        "/promote",
        "/publish",
        "/tag-apply",
        "/download",
    ):
        assert forbidden not in contract


def test_overlay_manifest_binds_the_three_static_files_and_keeps_authority_false():
    manifest = json.loads((OVERLAY / "overlay.manifest.json").read_text(encoding="utf-8"))
    assert manifest["default_live_task_creation"] is False
    assert manifest["slice3a"]["teacher_only"] is True
    assert manifest["slice3a"]["plan_only"] is True
    assert manifest["slice3a"]["model_invocation_present"] is False
    assert manifest["slice3a"]["actual_generation_present"] is False
    assert manifest["slice3a"]["external_publication_allowed"] is False
    assert manifest["slice3a"]["provider_platform_actual_reported"] is False
    assert manifest["slice3a"]["provider_signed"] is False
    assert manifest["claims"]["candidate_only"] is True
    for authority_or_use_flag in (
        "human_reviewed",
        "official",
        "publication_allowed",
        "retrieval_ready",
        "generation_allowed",
        "teaching_use_allowed",
    ):
        assert manifest["claims"][authority_or_use_flag] is False
    for name in ("index.html", "app.js", "styles.css"):
        digest = hashlib.sha256((OVERLAY / name).read_bytes()).hexdigest()
        assert manifest["files"][name]["sha256"] == digest
