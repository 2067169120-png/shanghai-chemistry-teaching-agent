from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from integrations.deeptutor_shchem_v1.config import ServingReleaseBinding
from integrations.deeptutor_shchem_v1.workbench_release_gateway import (
    WorkbenchReleaseGateway,
)

WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
INNER_MANIFEST = OVERLAY / "overlay.manifest.json"
OUTER_MANIFEST = OVERLAY.parent / "overlay.manifest.json"
OPENAPI = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
)

SHA_A = "a" * 64
SHA_B = "b" * 64
RELEASE_ID = f"WBREL-{'1' * 64}"
REVISION = f"WBREV-{'2' * 32}"
RUN_ID = f"WBRUN-{'3' * 32}"
TIMESTAMP = "2026-08-26T12:00:00Z"


def _validator(contract: dict[str, Any], schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/components/schemas/{schema_name}",
            "components": contract["components"],
        }
    )


def _assert_valid(
    contract: dict[str, Any], schema_name: str, instance: dict[str, Any]
) -> None:
    errors = sorted(
        _validator(contract, schema_name).iter_errors(instance),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    assert not errors, [error.message for error in errors]


def _bootstrap_serving() -> dict[str, Any]:
    return {
        "mode": "bootstrap_live",
        "release_id": None,
        "selected_revision": None,
        "candidate_manifest_sha256": None,
        "candidate_manifest_bytes": None,
        "closure_sha256": SHA_A,
        "browse_snapshot_id": None,
        "ui_build_id": SHA_B,
        "data_snapshot_id": SHA_A,
        "backend_build_id": None,
        "candidate_only": True,
        "human_reviewed": False,
        "teaching_use_allowed": False,
        "publication_allowed": False,
    }


def _regression_not_started() -> dict[str, Any]:
    return {
        "release_id": RELEASE_ID,
        "state": "not_started",
        "cancellable": False,
        "automatic_activation": False,
    }


def _candidate_summary() -> dict[str, Any]:
    return {
        "release_id": RELEASE_ID,
        "created_at": TIMESTAMP,
        "candidate_manifest_sha256": SHA_A,
        "candidate_manifest_bytes": 1024,
        "closure_sha256": SHA_B,
        "artifact_count": 12,
        "total_bytes": 4096,
        "candidate_only": True,
        "human_reviewed": False,
        "teaching_use_allowed": False,
        "publication_allowed": False,
    }


def _selected_release() -> dict[str, Any]:
    return {
        "release_id": RELEASE_ID,
        "revision": REVISION,
        "action": "activate",
        "updated_at": TIMESTAMP,
        "activation_sequence": 1,
        "candidate_manifest_sha256": SHA_A,
        "candidate_manifest_bytes": 1024,
    }


def _no_selected_release_trust() -> dict[str, Any]:
    return {
        "state": "none",
        "current_trusted": False,
        "replacement_trusted": False,
        "serving_eligible": False,
        "rollback_eligible": False,
        "replacement_only": False,
    }


def test_chinese_release_card_has_explicit_clean_restart_workflow_only() -> None:
    html = (OVERLAY / "index.html").read_text(encoding="utf-8")
    script = (OVERLAY / "app.js").read_text(encoding="utf-8")
    card = html.split('id="workbenchReleaseControlCard"', 1)[1].split(
        'id="modelProviderSettingsCard"', 1
    )[0]

    for text in (
        "版本与更新",
        "本次正在服务",
        "下次启动已选择",
        "需要安全重启",
        "冻结当前候选版本",
        "中文原因",
        "真人复核、官方、教学、生成和对外发布权限始终关闭",
    ):
        assert text in html or text in script
    for element_id in (
        "releaseControlRefresh",
        "releaseFreezeCandidate",
        "releaseCandidateList",
        "releaseSelectionReason",
        "releaseSelectionConfirm",
        "releaseSelectionSubmit",
    ):
        assert f'id="{element_id}"' in card
    for forbidden_input in (
        'name="command"',
        'name="path"',
        'name="environment"',
        'name="receipt"',
        'id="releaseCommand"',
        'id="releasePath"',
        'id="releaseReceipt"',
    ):
        assert forbidden_input not in card
    for fixed_question_count in ("65", "252", "470"):
        assert fixed_question_count not in card

    for endpoint in (
        "/api/v1/workbench/releases/status",
        "/api/v1/workbench/releases/candidates",
        "/regressions",
        "/cancel",
        "/select",
        "/rollback",
    ):
        assert endpoint in script
    assert "body: JSON.stringify({})" in script
    assert "body: JSON.stringify({ idempotency_key: releaseIdempotencyKey() })" in script
    assert "run_id: selection.runId" in script
    assert "expected_revision: expectedRevision" in script
    assert "reason_zh: reason" in script
    assert "browse_snapshot_id === null" in script
    assert "backend_build_id === null" in script
    assert 'selection_effect_policy !== "next_clean_restart_only"' in script
    assert "automatic_activation !== false" in script
    assert "client_supplied_commands_allowed !== false" in script
    assert "apiKey" not in card


def test_both_manifests_bind_release_boundaries_and_current_static_bytes() -> None:
    manifests = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (INNER_MANIFEST, OUTER_MANIFEST)
    ]
    inner_release = manifests[0]["workbench_release_control"]
    assert manifests[1]["workbench_release_control"] == inner_release
    assert inner_release["dynamic_candidate_counts"] is True
    assert inner_release["fixed_server_regression_recipe"] is True
    assert inner_release["selection_effect_policy"] == "next_clean_restart_only"
    assert inner_release["prepare_capability"] == "workbench_release_prepare_write"
    assert inner_release["activate_capability"] == "workbench_release_activate_write"
    for false_flag in (
        "client_supplied_commands_allowed",
        "client_supplied_paths_allowed",
        "client_supplied_environments_allowed",
        "client_supplied_receipts_allowed",
        "automatic_activation",
        "hot_swap_allowed",
        "api_key_included",
        "student_private_domain_included",
        "human_reviewed",
        "official",
        "retrieval_ready",
        "teaching_use_allowed",
        "generation_allowed",
        "publication_allowed",
        "external_release_allowed",
    ):
        assert inner_release[false_flag] is False
    assert inner_release["candidate_only"] is True

    for manifest in manifests:
        for name in ("index.html", "app.js", "styles.css"):
            raw = (OVERLAY / name).read_bytes()
            assert manifest["files"][name] == {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            }


def test_openapi_release_routes_and_request_dtos_are_strict() -> None:
    contract = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    assert contract["openapi"] == "3.1.0"
    assert contract["info"]["version"] == "1.25.0"
    paths = contract["paths"]
    expected_methods = {
        "/api/v1/workbench/releases/status": {"get"},
        "/api/v1/workbench/releases/candidates": {"get", "post"},
        "/api/v1/workbench/releases/{release_id}/regressions": {"post"},
        "/api/v1/workbench/releases/{release_id}/regressions/{run_id}": {"get"},
        "/api/v1/workbench/releases/{release_id}/regressions/{run_id}/cancel": {
            "post"
        },
        "/api/v1/workbench/releases/{release_id}/select": {"post"},
        "/api/v1/workbench/releases/{release_id}/rollback": {"post"},
    }
    for route, methods in expected_methods.items():
        assert set(paths[route]).intersection({"get", "post", "put", "patch", "delete"}) == methods

    trust = contract["components"]["schemas"]["WorkbenchSelectedReleaseTrust"]
    assert trust["additionalProperties"] is False
    assert set(trust["required"]) == {
        "state",
        "current_trusted",
        "replacement_trusted",
        "serving_eligible",
        "rollback_eligible",
        "replacement_only",
    }
    status_schema = contract["components"]["schemas"]["WorkbenchReleaseStatusData"]
    assert "selected_release_trust" in status_schema["required"]
    assert status_schema["additionalProperties"] is False

    requests = {
        "WorkbenchReleaseEmptyRequest": {},
        "WorkbenchReleaseRegressionStartRequest": {
            "idempotency_key": "release-ui:1234567890abcdef"
        },
        "WorkbenchReleaseSelectionRequest": {
            "run_id": RUN_ID,
            "expected_revision": None,
            "reason_zh": "固定回归通过，选择供下次启动。",
        },
        "WorkbenchReleaseRollbackRequest": {
            "run_id": RUN_ID,
            "expected_revision": REVISION,
            "reason_zh": "回退到已验证的历史候选版本。",
        },
    }
    for schema_name, instance in requests.items():
        _assert_valid(contract, schema_name, instance)
        for forbidden_field in ("command", "path", "environment", "receipt"):
            mutated = {**instance, forbidden_field: "not-allowed"}
            assert list(_validator(contract, schema_name).iter_errors(mutated))
    english_only = {**requests["WorkbenchReleaseSelectionRequest"], "reason_zh": "PASS"}
    assert list(
        _validator(contract, "WorkbenchReleaseSelectionRequest").iter_errors(
            english_only
        )
    )


def test_openapi_validates_release_envelopes_and_rejects_mixed_bootstrap_identity() -> None:
    contract = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    status = {
        "contract_version": "shchem.gateway.v1",
        "request_id": "req-status",
        "data": {
            "schema_version": "shchem.workbench.release_gateway.v1",
            "serving_release": _bootstrap_serving(),
            "selected_release": None,
            "selected_release_trust": _no_selected_release_trust(),
            "restart_required": False,
            "selection_effect_policy": "next_clean_restart_only",
            "active_regression": None,
            "automatic_activation": False,
            "client_supplied_commands_allowed": False,
            "candidate_only": True,
            "human_reviewed": False,
            "teaching_use_allowed": False,
            "publication_allowed": False,
        },
    }
    _assert_valid(contract, "WorkbenchReleaseStatusEnvelope", status)
    inconsistent = copy.deepcopy(status)
    inconsistent["data"]["selected_release_trust"]["serving_eligible"] = True
    assert list(
        _validator(contract, "WorkbenchReleaseStatusEnvelope").iter_errors(
            inconsistent
        )
    )
    unexpected = copy.deepcopy(status)
    unexpected["data"]["selected_release_trust"]["mode"] = "none"
    assert list(
        _validator(contract, "WorkbenchReleaseStatusEnvelope").iter_errors(
            unexpected
        )
    )
    for forbidden_bootstrap_field in ("browse_snapshot_id", "backend_build_id"):
        mixed = copy.deepcopy(status)
        mixed["data"]["serving_release"][forbidden_bootstrap_field] = SHA_A
        assert list(
            _validator(contract, "WorkbenchReleaseStatusEnvelope").iter_errors(
                mixed
            )
        )
    candidate = _candidate_summary()
    regression = _regression_not_started()
    list_candidate = {**candidate, "regression": regression}
    envelopes = {
        "WorkbenchReleaseCandidateListEnvelope": {
            "contract_version": "shchem.gateway.v1",
            "request_id": "req-list",
            "data": {
                "schema_version": "shchem.workbench.release_gateway.v1",
                "items": [list_candidate],
                "count": 1,
                "candidate_only": True,
                "automatic_activation": False,
            },
        },
        "WorkbenchReleaseFreezeEnvelope": {
            "contract_version": "shchem.gateway.v1",
            "request_id": "req-freeze",
            "data": {
                "candidate": candidate,
                "regression": regression,
                "selection_committed": False,
                "serving_changed": False,
                "automatic_activation": False,
            },
        },
        "WorkbenchReleaseRegressionStartEnvelope": {
            "contract_version": "shchem.gateway.v1",
            "request_id": "req-regression",
            "data": {
                "run": {
                    "run_id": RUN_ID,
                    "release_id": RELEASE_ID,
                    "state": "running",
                    "cancellable": True,
                    "automatic_activation": False,
                },
                "idempotent": False,
            },
        },
        "WorkbenchReleaseRegressionCancelEnvelope": {
            "contract_version": "shchem.gateway.v1",
            "request_id": "req-cancel",
            "data": {
                "run": {
                    "run_id": RUN_ID,
                    "release_id": RELEASE_ID,
                    "state": "running",
                    "cancellation_requested": True,
                    "cancellable": True,
                    "automatic_activation": False,
                },
                "automatic_activation": False,
            },
        },
        "WorkbenchReleaseSelectionEnvelope": {
            "contract_version": "shchem.gateway.v1",
            "request_id": "req-select",
            "data": {
                "selected_release": _selected_release(),
                "selection_committed": True,
                "serving_changed": False,
                "restart_required": True,
                "effect_message_zh": "版本选择已提交；安全重启后生效。",
                "candidate_only": True,
                "human_reviewed": False,
                "teaching_use_allowed": False,
                "publication_allowed": False,
            },
        },
    }
    for schema_name, instance in envelopes.items():
        _assert_valid(contract, schema_name, instance)

    overclaim = copy.deepcopy(envelopes["WorkbenchReleaseSelectionEnvelope"])
    overclaim["data"]["teaching_use_allowed"] = True
    assert list(
        _validator(contract, "WorkbenchReleaseSelectionEnvelope").iter_errors(
            overclaim
        )
    )


def test_openapi_validates_real_release_status_with_null_selected_release(
    tmp_path: Path,
) -> None:
    contract = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    serving = ServingReleaseBinding(
        mode="bootstrap_live",
        release_id=None,
        selected_revision=None,
        candidate_manifest_sha256=None,
        candidate_manifest_bytes=None,
        closure_sha256=SHA_A,
        browse_snapshot_id=None,
        ui_build_id=SHA_B,
        data_snapshot_id=SHA_A,
        backend_build_id=None,
    )
    serving.validate()
    gateway = WorkbenchReleaseGateway(
        WORKSPACE, tmp_path / "release-status-contract", serving
    )
    try:
        status_data = gateway.status()
    finally:
        gateway.shutdown()
    assert status_data["selected_release"] is None
    assert status_data["selected_release_trust"] == _no_selected_release_trust()
    _assert_valid(
        contract,
        "WorkbenchReleaseStatusEnvelope",
        {
            "contract_version": "shchem.gateway.v1",
            "request_id": "req-real-release-status",
            "data": status_data,
        },
    )
