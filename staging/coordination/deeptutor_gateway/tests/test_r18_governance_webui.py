from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1.config import Principal, token_digest
from integrations.deeptutor_shchem_v1.r18_governance_view import (
    R18GovernanceView,
    _safe_summary,
)
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    TOKEN_A,
    build_config,
    request,
    running_server,
)


WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
OPENAPI = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
)
STUDENT_TOKEN = "r18-governance-student-0123456789"


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def write_json(workspace: Path, relative: str, value: dict) -> dict:
    path = workspace.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_bytes(value)
    path.write_bytes(data)
    return {
        "path": relative,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }


def with_output_hash(value: dict) -> dict:
    result = dict(value)
    result["output_sha256"] = hashlib.sha256(canonical_bytes(result)).hexdigest()
    return result


def build_future_fixture(temp: Path, mutation: str | None = None):
    workspace = temp
    root = workspace / "sh-chem-db"
    root.mkdir(parents=True)
    question_ref = write_json(
        workspace,
        "sh-chem-db/tests/generation_publication_v2/controller_fixture_r18/subject_question.json",
        {"paper_id": "PAPER-R18-FUTURE", "atomic_ids": ["P01", "P02"]},
    )
    answer_ref = write_json(
        workspace,
        "sh-chem-db/tests/generation_publication_v2/controller_fixture_r18/subject_answer.json",
        {"paper_id": "PAPER-R18-FUTURE", "answers": ["A", "B"]},
    )
    if mutation == "question_subject_hash":
        question_ref = dict(question_ref, sha256="e" * 64)
    if mutation == "question_subject_bytes":
        question_ref = dict(question_ref, bytes=question_ref["bytes"] + 1)
    if mutation == "question_subject_path_case":
        question_ref = dict(
            question_ref,
            path=question_ref["path"].replace("subject_question.json", "SUBJECT_QUESTION.json"),
        )
    subject = {
        "question": question_ref,
        "answer": answer_ref,
        "subject_pair_sha256": hashlib.sha256(
            (question_ref["sha256"] + answer_ref["sha256"]).encode("ascii")
        ).hexdigest(),
    }
    scope = {"declared_atomic_part_ids": ["P01", "P02"]}
    request_value = {"subject": subject, "atomic_scope": scope}
    request_ref = write_json(
        workspace,
        "sh-chem-db/tests/generation_publication_v2/controller_fixture_r18/deterministic_check_request.json",
        request_value,
    )
    activation_request_ref = request_ref
    if mutation == "deterministic_request_hash":
        activation_request_ref = dict(request_ref, sha256="d" * 64)
    if mutation == "deterministic_request_bytes":
        activation_request_ref = dict(request_ref, bytes=request_ref["bytes"] + 1)
    report_ref = write_json(
        workspace,
        "sh-chem-db/tests/generation_publication_v2/controller_fixture_r18/deterministic_check_report.json",
        {
            "request_sha256": request_ref["sha256"],
            "subject": subject,
            "atomic_scope": scope,
            "passed": True,
            "human_reviewed": False,
            "errors": [],
        },
    )
    review_schema_ref = write_json(
        workspace,
        "sh-chem-db/kb/machine_governance_v2/schemas/machine_review_record.schema.json",
        {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"},
    )
    activation_id = "PHASE1-ACTIVATION-R18-FUTURE-0001"
    activation_dir = (
        "staging/coordination/generation_publication/r18/review_dispatch/"
        + activation_id
    )
    output_root = (
        "sh-chem-db/tests/generation_publication_v2/controller_fixture_r18/"
        f"review_attempts/{activation_id}"
    )
    tasks = [
        {"slot": slot, "output_path": f"{output_root}/{slot}.json"}
        for slot in ("sol_review_a", "sol_review_b", "adversarial_check")
    ]
    if mutation == "legacy_root_receipt":
        tasks[0]["output_path"] = (
            "sh-chem-db/tests/generation_publication_v2/controller_fixture_r18/sol_review_a.json"
        )
    dispatch_value = {"requested_tasks": tasks}
    dispatch_ref = write_json(
        workspace, f"{activation_dir}/review_dispatch_request_r18.json", dispatch_value
    )
    dispatch_core = {
        "canonical_sha256": dispatch_ref["sha256"],
        "canonical_bytes": dispatch_ref["bytes"],
    }
    activation_value = {
        "immutable_inputs": {
            "question_subject": question_ref,
            "answer_subject": answer_ref,
            "deterministic_request": activation_request_ref,
            "deterministic_report": report_ref,
        },
        "schemas": {"machine_review_record": review_schema_ref},
        "dispatch_core": dispatch_core,
    }
    activation_ref = write_json(
        workspace, f"{activation_dir}/review_phase1_activation_r18.json", activation_value
    )
    selector_activation_ref = activation_ref
    if mutation == "activation_hash":
        selector_activation_ref = dict(activation_ref, sha256="c" * 64)
    if mutation == "activation_bytes":
        selector_activation_ref = dict(activation_ref, bytes=activation_ref["bytes"] + 1)
    if mutation == "activation_path_case":
        selector_activation_ref = dict(
            activation_ref,
            path=activation_ref["path"].replace(
                "review_phase1_activation_r18.json", "REVIEW_PHASE1_ACTIVATION_R18.json"
            ),
        )

    findings = {
        "sol_review_a": [
            {
                "atomic_part_id": "P01",
                "verdict": "fail",
                "checks": {
                    "chemistry": "fail",
                    "answer": "pass",
                    "rubric": "pass",
                    "shanghai_style": "pass",
                    "ambiguity": "fail",
                },
                "finding": "The condition is under-specified; two chemically distinct readings remain.",
                "evidence": "private evidence must never be projected",
            },
            {
                "atomic_part_id": "P02",
                "verdict": "pass",
                "checks": {"chemistry": "pass"},
                "finding": "pass",
                "evidence": "private evidence",
            },
        ],
        "sol_review_b": [
            {
                "atomic_part_id": atomic_id,
                "verdict": "pass",
                "checks": {"chemistry": "pass"},
                "finding": "pass",
                "evidence": "private evidence",
            }
            for atomic_id in ("P01", "P02")
        ],
    }
    receipt_refs = {}
    for slot in ("sol_review_a", "sol_review_b"):
        receipt = {
            "slot": slot,
            "subject": subject,
            "deterministic_report_sha256": report_ref["sha256"],
            "verdict": "fail" if slot == "sol_review_a" else "pass",
            "findings": findings[slot],
            "human_reviewed": False,
        }
        if mutation == f"{slot}_subject":
            receipt["subject"] = dict(subject, subject_pair_sha256="0" * 64)
        if mutation == f"{slot}_slot":
            receipt["slot"] = "sol_review_b" if slot == "sol_review_a" else "sol_review_a"
        receipt = with_output_hash(receipt)
        receipt_refs[slot] = write_json(
            workspace, f"{output_root}/{slot}.json", receipt
        )

    for slot in ("sol_review_a", "sol_review_b"):
        attestation = {
            "reviewer_slot": slot,
            "phase1_activation": activation_ref,
            "allowed_output_path": f"{output_root}/{slot}.json",
        }
        attestation_ref = write_json(
            workspace,
            "staging/coordination/root/revisions/r18/review_task_attestations/"
            f"{slot}--fixture/review_task_creation_attestation_r18.json",
            attestation,
        )
        observation = {
            "reviewer_slot": slot,
            "task_creation_attestation": attestation_ref,
            "review_receipt": receipt_refs[slot],
        }
        if mutation == f"{slot}_observation_hash":
            observation["review_receipt"] = dict(receipt_refs[slot], sha256="f" * 64)
        if mutation != f"{slot}_observation_missing":
            write_json(
                workspace,
                "staging/coordination/root/revisions/r18/review_metadata_observations/"
                f"{slot}--fixture/review_codex_metadata_observation_r18.json",
                observation,
            )
    write_json(
        workspace,
        "sh-chem-db/kb/machine_governance_v2/chain_registry.json",
        {"chains": [], "human_reviewed": False},
    )
    selector = lambda *_args, **_kwargs: {
        "valid": True,
        "selected": {"state": "phase1_activation"},
        "activation": selector_activation_ref,
        "legacy_fallback_used": False,
        "errors": [],
    }
    view = R18GovernanceView(root, selector=selector)
    view._validate_support_with_controller = lambda *_args, **_kwargs: []
    return view


def slot(value: dict, name: str) -> dict:
    return next(item for item in value["slots"] if item["slot"] == name)


def assert_redacted(value: dict) -> None:
    serialized = json.dumps(value, ensure_ascii=False).casefold()
    for forbidden in (
        "prompt",
        "thread_id",
        "turn_id",
        "host_id",
        "evidence must never",
        "private evidence",
        "c:\\users",
        "staging/",
        "sh-chem-db/",
    ):
        assert forbidden not in serialized


def test_activation_scoped_future_fixture_projects_fail_and_pass_without_authority():
    with tempfile.TemporaryDirectory() as temp:
        value = build_future_fixture(Path(temp)).current()
    assert value["active_selector"]["selected"] is True
    assert slot(value, "prefreeze_deterministic_qa")["verdict"] == "pass"
    review_a = slot(value, "review_a")
    assert review_a["verdict"] == "fail"
    assert review_a["failed_atomic_part_ids"] == ["P01"]
    assert review_a["check_axes"] == ["ambiguity", "chemistry"]
    assert slot(value, "review_b")["verdict"] == "pass"
    assert slot(value, "adversarial")["state"] == "missing"
    assert slot(value, "chain")["state"] == "absent"
    assert slot(value, "registration")["state"] == "absent"
    assert value["phase2_enabled"] is False
    assert value["download_available"] is False
    assert value["registration_allowed"] is False
    assert_redacted(value)


@pytest.mark.parametrize(
    "mutation,slot_name,reason",
    [
        ("sol_review_a_subject", "review_a", "subject_mismatch"),
        ("sol_review_a_slot", "review_a", "slot_mismatch"),
        ("sol_review_a_observation_hash", "review_a", "hash_mismatch"),
        ("sol_review_a_observation_missing", "review_a", "observation_missing"),
        ("legacy_root_receipt", "review_a", "legacy_root_receipt_rejected"),
    ],
)
def test_receipt_and_binding_mutations_fail_closed(mutation, slot_name, reason):
    with tempfile.TemporaryDirectory() as temp:
        value = build_future_fixture(Path(temp), mutation).current()
    projected = slot(value, slot_name)
    assert projected["verdict"] == "blocked"
    assert reason in projected["reason_codes"]
    assert projected["failed_atomic_part_ids"] == []
    assert_redacted(value)


@pytest.mark.parametrize(
    "mutation",
    [
        "question_subject_hash",
        "question_subject_bytes",
        "question_subject_path_case",
        "deterministic_request_hash",
        "deterministic_request_bytes",
        "activation_hash",
        "activation_bytes",
        "activation_path_case",
    ],
)
def test_activation_subject_and_request_reference_mutations_fail_closed(mutation):
    with tempfile.TemporaryDirectory() as temp:
        value = build_future_fixture(Path(temp), mutation).current()
    assert value["active_selector"]["selected"] is False or slot(
        value, "prefreeze_deterministic_qa"
    )["verdict"] == "blocked"
    assert slot(value, "review_a")["verdict"] == "blocked"
    assert_redacted(value)


def test_selector_ambiguity_and_mixed_generation_never_fallback_or_write():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "sh-chem-db"
        root.mkdir()
        marker = root / "marker.txt"
        marker.write_text("unchanged", encoding="utf-8")
        before = (marker.read_bytes(), marker.stat().st_mtime_ns, sorted(root.rglob("*")))
        view = R18GovernanceView(
            root,
            selector=lambda *_args, **_kwargs: {
                "valid": False,
                "selected": None,
                "legacy_fallback_used": False,
                "errors": [
                    "active_select:latest_package_ambiguous",
                    "formal_freeze:r18_producer_receipt:sha256_mismatch",
                    "formal_freeze:sol_generator_receipt:sha256_mismatch",
                    "active_freeze:invalidation_corpus:head_live_fingerprint_mismatch",
                ],
            },
        )
        value = view.current()
        after = (marker.read_bytes(), marker.stat().st_mtime_ns, sorted(root.rglob("*")))
    assert before == after
    assert value["active_selector"]["selected"] is False
    assert value["active_selector"]["legacy_fallback_used"] is False
    assert "active_selector_ambiguous" in value["active_selector"]["reason_codes"]
    assert "partial_candidate_rebuild" in value["active_selector"]["reason_codes"]
    assert slot(value, "review_a")["state"] == "blocked"
    assert_redacted(value)


@pytest.mark.parametrize(
    "private_text",
    [
        "hidden prompt text",
        "private evidence text",
        "thread metadata",
        "turn metadata",
        "host metadata",
    ],
)
def test_finding_summary_redacts_private_control_plane_words(private_text):
    assert _safe_summary(private_text) == "[unsafe detail redacted]"


def test_subject_binding_includes_exact_path_not_only_hash_and_bytes():
    reference = {"path": "subject.json", "sha256": "1" * 64, "bytes": 1}
    left = {
        "question": reference,
        "answer": reference,
        "subject_pair_sha256": "2" * 64,
    }
    right = {
        **left,
        "question": {**reference, "path": "SUBJECT.json"},
    }
    assert R18GovernanceView._subjects_match(left, right) is False


def test_dispatch_output_path_case_mutation_is_blocked():
    with tempfile.TemporaryDirectory() as temp:
        view = build_future_fixture(Path(temp))
        workspace = Path(temp)
        activation_path = next(
            workspace.glob(
                "staging/coordination/generation_publication/r18/review_dispatch/"
                "*/review_phase1_activation_r18.json"
            )
        )
        dispatch_path = activation_path.with_name("review_dispatch_request_r18.json")
        activation = json.loads(activation_path.read_text(encoding="utf-8"))
        dispatch = json.loads(dispatch_path.read_text(encoding="utf-8"))
        original = dispatch["requested_tasks"][0]["output_path"]
        dispatch["requested_tasks"][0]["output_path"] = original.replace(
            "/sol_review_a.json", "/SOL_REVIEW_A.json"
        )
        raw = canonical_bytes(dispatch)
        dispatch_path.write_bytes(raw)
        activation["dispatch_core"] = {
            "canonical_sha256": hashlib.sha256(raw).hexdigest(),
            "canonical_bytes": len(raw),
        }
        activation_ref = {
            "path": activation_path.relative_to(workspace).as_posix(),
            "sha256": "0" * 64,
            "bytes": 0,
        }
        paths, _, errors = view._activation_paths(activation_ref, activation)
    assert "legacy_or_non_activation_scoped_receipt" in errors
    assert "sol_review_a" not in paths


def test_governance_api_is_teacher_bearer_and_origin_scoped():
    with tempfile.TemporaryDirectory() as temp:
        state_root = Path(temp)
        config = build_config(state_root)
        config.principals.append(
            Principal("r18-student", "student", token_digest(STUDENT_TOKEN), ())
        )
        with running_server(config) as server:
            server.service.r18_governance._selector = lambda *_args, **_kwargs: {
                "valid": False,
                "selected": None,
                "legacy_fallback_used": False,
                "errors": ["active_selector_blocked"],
            }
            route = "/api/v1/generation/runs/current/governance"
            status, body, _ = request(server, "GET", route, token=None)
            assert status == 401
            assert body["error"]["code"] == "authentication_required"
            status, body, _ = request(
                server,
                "GET",
                route,
                token=STUDENT_TOKEN,
                headers={"Origin": f"http://127.0.0.1:{server.server_address[1]}"},
            )
            assert status == 403
            assert body["error"]["code"] == "teacher_scope_required"
            status, body, _ = request(server, "GET", route)
            assert status == 403
            assert body["error"]["code"] == "origin_required"
            status, body, _ = request(
                server, "GET", route, headers={"Origin": "https://evil.example"}
            )
            assert status == 403
            assert body["error"]["code"] == "origin_denied"
            origin = f"http://127.0.0.1:{server.server_address[1]}"
            status, body, _ = request(
                server,
                "GET",
                route,
                headers={
                    "Referer": origin + "/overlay/#/generation",
                    "Sec-Fetch-Site": "same-origin",
                },
            )
            assert status == 200
            assert body["data"]["read_only"] is True
            before = {
                path.relative_to(state_root).as_posix(): (
                    path.read_bytes(),
                    path.stat().st_mtime_ns,
                )
                for path in state_root.rglob("*")
                if path.is_file()
            }
            status, body, _ = request(
                server,
                "GET",
                route,
                headers={"Origin": origin, "Sec-Fetch-Site": "same-origin"},
            )
            assert status == 200
            assert body["data"]["read_only"] is True
            assert_redacted(body["data"])
            after = {
                path.relative_to(state_root).as_posix(): (
                    path.read_bytes(),
                    path.stat().st_mtime_ns,
                )
                for path in state_root.rglob("*")
                if path.is_file()
            }
            assert after == before


def test_webui_contract_reset_and_manifest_claims_are_explicit():
    html = (OVERLAY / "index.html").read_text(encoding="utf-8")
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    css = (OVERLAY / "styles.css").read_text(encoding="utf-8")
    contract = OPENAPI.read_text(encoding="utf-8")
    manifest = json.loads((OVERLAY / "overlay.manifest.json").read_text(encoding="utf-8"))
    assert "/api/v1/generation/runs/current/governance" in contract
    assert "trusted loopback Origin" in contract
    assert "governanceSlots" in html and "governanceSelector" in html
    assert "state.currentGovernance = null" in app
    assert "resetBlock(\"governanceSlots\"" in app
    assert "phase2_enabled" in app and "download_available" in app
    assert "@media (max-width: 760px)" in css
    governance = manifest["r18_governance_webui"]
    assert governance["teacher_only"] is True
    assert governance["read_only"] is True
    assert governance["legacy_fallback"] is False
    assert governance["phase2_enabled"] is False
    assert governance["download_present"] is False
    assert governance["write_endpoint_present"] is False
