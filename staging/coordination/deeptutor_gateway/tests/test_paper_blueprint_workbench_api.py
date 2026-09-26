from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from integrations.deeptutor_shchem_v1.paper_blueprint_workbench import (
    PaperBlueprintPreviewStore,
)
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    TOKEN_A,
    TOKEN_B,
    build_config,
    request,
    running_server,
)
from staging.coordination.deeptutor_gateway.tests.test_paper_export_workbench_api import (
    SNAPSHOT,
    _catalog,
)


def _origin(server: Any) -> dict[str, str]:
    return {
        "Origin": f"http://127.0.0.1:{server.server_address[1]}",
        "Sec-Fetch-Site": "same-origin",
    }


def _payload(*, mode: str = "mock_exam") -> dict[str, Any]:
    paper: dict[str, Any]
    if mode == "mock_exam":
        paper = {
            "exam_name_zh": "合成主题测试",
            "subtitle_zh": "仅用于后端合同测试",
            "template_id": "shanghai_theme_paper_project_template",
            "template_version": "1.0.0",
            "template_year": 2031,
            "duration_minutes": 2,
            "duration_rule": "exact",
            "total_score": 4,
            "theme_count": 1,
            "instructions_zh": ["本卷为合成软件测试。"],
            "scoring_rules": {
                "selection_rule_zh": "按题面要求计分。",
                "partial_credit_rule_zh": "按步骤建议计分。",
                "other_rule_zh": "不是官方评分细则。",
            },
            "identity_fields_zh": ["姓名", "班级"],
            "sealed_line": False,
            "numbering_mode": "restart_within_each_theme",
            "answer_space_lines": 3,
            "score_per_atomic": 2,
            "time_per_atomic_minutes": 1,
            "pagination_rules": {"cover_page": True},
        }
    else:
        paper = {
            "title_zh": "合成日常练习",
            "subtitle_zh": "仅用于后端合同测试",
            "numbering_mode": "restart_within_each_theme",
            "answer_space_lines": 3,
            "score_per_atomic": 2,
            "time_per_atomic_minutes": 1,
        }
    return {
        "mode": mode,
        "scope": "master",
        "data_snapshot_id": SNAPSHOT,
        "candidate_count": 1,
        "paper": paper,
        "hard_constraints": {
            "answer_eligibility": {
                "allowed_availability": ["present_part_aligned"],
                "allowed_authorities": ["nonofficial"],
                "require_all_selected": True,
            },
            "no_duplicate_clusters": False,
        },
        "preferences": {"selection_unit": "theme"},
        "ordering": {
            "teacher_theme_order": [],
            "prerequisite_edges": [],
        },
        "search": {},
    }


def _configure(
    server: Any, *, catalog: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    captured_exports: list[dict[str, Any]] = []
    catalog = deepcopy(catalog or _catalog())
    server.service._paper_export_scope_snapshot_id = (
        lambda principal, scope: SNAPSHOT
    )
    server.service.theme_workbench_groups = (
        lambda principal, *, scope: deepcopy(catalog)
    )
    server.service.textbook_catalog = lambda principal: {
        "schema_version": "synthetic-curriculum.v1",
        "data_snapshot_id": "d" * 64,
        "volumes": [],
    }
    server.service._curriculum_search = lambda selector: {
        "schema_version": "synthetic-curriculum-mapping.v1",
        "items": [],
    }

    def start(payload: dict[str, Any], **callbacks: Any) -> dict[str, Any]:
        del callbacks
        captured_exports.append(deepcopy(payload))
        return {
            "schema_version": "shchem.paper-export-workbench-job.v1",
            "job_id": "WBEXP-" + "e" * 32,
            "status": "queued",
            "created_at": "2026-08-28T00:00:00Z",
            "updated_at": "2026-08-28T00:00:00Z",
            "scope": "master",
            "data_snapshot_id": SNAPSHOT,
            "title_zh": payload["title_zh"],
            "request_digest": "f" * 64,
            "progress": {
                "stage": "queued",
                "percent": 0,
                "message_zh": "已进入本机导出队列。",
            },
            "counts": {},
            "artifacts": [],
            "error": None,
        }

    server.service.paper_export_jobs.start = start
    server.service.paper_export_jobs.get = lambda job_id: start(
        {
            "title_zh": "合成主题测试",
            "selections": [],
        }
    )
    return captured_exports


def _preview(server: Any, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    status, body, _ = request(
        server,
        "POST",
        "/api/v1/prep/blueprints/preview",
        token=TOKEN_A,
        headers=_origin(server),
        payload=payload or _payload(),
    )
    assert status == 200, body
    assert body["data"]["status"] == "feasible"
    return body["data"]


def _approve(server: Any, candidate: dict[str, Any], *, token: str = TOKEN_A):
    return request(
        server,
        "POST",
        "/api/v1/prep/blueprints/approve",
        token=token,
        headers=_origin(server),
        payload={
            "candidate_id": candidate["candidate_id"],
            "preview_snapshot_sha256": candidate["preview_snapshot_sha256"],
            "data_snapshot_id": SNAPSHOT,
        },
    )


def test_preview_is_owner_bound_strict_and_hides_server_only_plan(tmp_path: Path) -> None:
    with running_server(build_config(tmp_path / "gateway-state")) as server:
        _configure(server)
        preview = _preview(server)
        candidate = preview["candidates"][0]
        assert preview["preview_revision"].startswith("PBREV-")
        assert candidate["expires_at"] == preview["candidate_expires_at"]
        assert "basket_selections" not in candidate
        assert "assembly_blueprint" not in candidate
        assert candidate["preview_model"]["projection_unit"] == (
            "theme_big_question"
        )
        assert candidate["preview_model"]["theme_groups"][0][
            "printed_questions"
        ]
        assert "C:\\" not in str(preview)

        status, body, _ = _approve(server, candidate, token=TOKEN_B)
        assert status == 404
        assert body["error"]["code"] == "paper_blueprint_candidate_not_found"

        bad = _payload()
        bad["client_selections_sha256"] = "0" * 64
        status, body, _ = request(
            server,
            "POST",
            "/api/v1/prep/blueprints/preview",
            headers=_origin(server),
            payload=bad,
        )
        assert status == 400
        assert body["error"]["code"] == "paper_blueprint_request_invalid"


def test_approve_is_hash_bound_and_idempotent(tmp_path: Path) -> None:
    with running_server(build_config(tmp_path / "gateway-state")) as server:
        _configure(server)
        candidate = _preview(server)["candidates"][0]
        status, body, _ = request(
            server,
            "POST",
            "/api/v1/prep/blueprints/approve",
            headers=_origin(server),
            payload={
                "candidate_id": candidate["candidate_id"],
                "preview_snapshot_sha256": candidate[
                    "preview_snapshot_sha256"
                ],
                "data_snapshot_id": SNAPSHOT,
                "client_revision": "forbidden",
            },
        )
        assert status == 400
        assert body["error"]["code"] == (
            "paper_blueprint_approval_request_invalid"
        )

        wrong = deepcopy(candidate)
        wrong["preview_snapshot_sha256"] = "0" * 64
        status, body, _ = _approve(server, wrong)
        assert status == 409
        assert body["error"]["code"] == (
            "paper_blueprint_approval_hash_mismatch"
        )

        status, first, _ = _approve(server, candidate)
        assert status == 200
        status, second, _ = _approve(server, candidate)
        assert status == 200
        assert first["data"]["preview_approval_id"] == second["data"][
            "preview_approval_id"
        ]
        assert first["data"]["approved_revision"] == second["data"][
            "approved_revision"
        ]
        assert first["data"]["idempotent_replay"] is False
        assert second["data"]["idempotent_replay"] is True


def test_changed_preview_revokes_old_approval_even_after_aba(tmp_path: Path) -> None:
    with running_server(build_config(tmp_path / "gateway-state")) as server:
        _configure(server)
        original_payload = _payload()
        original = _preview(server, original_payload)
        candidate = original["candidates"][0]
        status, approved, _ = _approve(server, candidate)
        assert status == 200

        changed_payload = _payload()
        changed_payload["paper"]["answer_space_lines"] = 2
        changed = _preview(server, changed_payload)
        assert changed["preview_revision"] != original["preview_revision"]
        reverted = _preview(server, original_payload)
        assert reverted["preview_revision"] != original["preview_revision"]

        approval_id = approved["data"]["preview_approval_id"]
        status, body, _ = request(
            server,
            "POST",
            f"/api/v1/prep/blueprints/{approval_id}/export",
            headers=_origin(server),
            payload={},
        )
        assert status == 409
        assert body["error"]["code"] == "paper_blueprint_approval_stale"


def test_approved_export_uses_only_saved_selections_and_bridges_four_file_job(
    tmp_path: Path,
) -> None:
    with running_server(build_config(tmp_path / "gateway-state")) as server:
        captured = _configure(server)
        preview = _preview(server)
        candidate = preview["candidates"][0]
        status, approved, _ = _approve(server, candidate)
        assert status == 200
        approval_id = approved["data"]["preview_approval_id"]

        status, body, _ = request(
            server,
            "POST",
            f"/api/v1/prep/blueprints/{approval_id}/export",
            headers=_origin(server),
            payload={"selections": []},
        )
        assert status == 400
        assert body["error"]["code"] == (
            "paper_blueprint_export_request_invalid"
        )
        assert captured == []

        status, body, _ = request(
            server,
            "POST",
            f"/api/v1/prep/blueprints/{approval_id}/export",
            headers=_origin(server),
            payload={},
        )
        assert status == 202, body
        assert body["data"]["job"]["job_id"] == "WBEXP-" + "e" * 32
        assert body["data"]["preview_approval_id"] == approval_id
        assert len(captured) == 1
        assert captured[0]["selections"] == [
            {
                "scope": "master",
                "selection_unit": "theme",
                "theme_id": "T1",
                "target_atomic_id": None,
                "expected_data_snapshot_id": SNAPSHOT,
            }
        ]
        assert captured[0]["score_per_atomic"] == 2
        assert captured[0]["answer_space_lines"] == 3


def test_direct_stale_cross_owner_and_expired_exports_fail_closed(
    tmp_path: Path,
) -> None:
    now = [100.0]
    with running_server(build_config(tmp_path / "gateway-state")) as server:
        _configure(server)
        server.service.paper_blueprint_previews = PaperBlueprintPreviewStore(
            tmp_path / "ttl-state",
            ttl_seconds=1,
            clock=lambda: now[0],
        )
        candidate = _preview(server)["candidates"][0]
        status, approved, _ = _approve(server, candidate)
        assert status == 200
        approval_id = approved["data"]["preview_approval_id"]

        status, body, _ = request(
            server,
            "POST",
            f"/api/v1/prep/blueprints/{approval_id}/export",
            token=TOKEN_B,
            headers=_origin(server),
            payload={},
        )
        assert status == 404
        assert body["error"]["code"] == "paper_blueprint_approval_not_found"

        now[0] = 102.0
        status, body, _ = request(
            server,
            "POST",
            f"/api/v1/prep/blueprints/{approval_id}/export",
            headers=_origin(server),
            payload={},
        )
        assert status == 410
        assert body["error"]["code"] == "paper_blueprint_approval_expired"

        status, body, _ = request(
            server,
            "POST",
            "/api/v1/prep/blueprints/PBAPP-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/export",
            headers=_origin(server),
            payload={},
        )
        assert status == 404
        assert body["error"]["code"] == "paper_blueprint_approval_not_found"


def test_expired_preview_cannot_be_approved(tmp_path: Path) -> None:
    now = [10.0]
    with running_server(build_config(tmp_path / "gateway-state")) as server:
        _configure(server)
        server.service.paper_blueprint_previews = PaperBlueprintPreviewStore(
            tmp_path / "ttl-preview-state",
            ttl_seconds=1,
            clock=lambda: now[0],
        )
        candidate = _preview(server)["candidates"][0]
        now[0] = 12.0
        status, body, _ = _approve(server, candidate)
        assert status == 410
        assert body["error"]["code"] == "paper_blueprint_candidate_expired"


def test_both_modes_and_manual_basket_use_real_theme_projection(tmp_path: Path) -> None:
    with running_server(build_config(tmp_path / "gateway-state")) as server:
        _configure(server)
        daily = _payload(mode="daily_practice")
        daily["basket_selections"] = [
            {
                "scope": "master",
                "selection_unit": "dependency",
                "theme_id": "T1",
                "target_atomic_id": "A2",
                "expected_data_snapshot_id": SNAPSHOT,
            }
        ]
        preview = _preview(server, daily)
        candidate = preview["candidates"][0]
        assert preview["mode"] == "daily_practice"
        assert candidate["preview_model"]["layout_kind"] == "compact_practice"
        assert candidate["coverage"]["totals"]["atomic_count"] == 2
        assert [
            row["data_ref"]["atomic_part_id"]
            for row in candidate["preview_model"]["theme_groups"][0][
                "compact_rows"
            ]
        ] == ["A1", "A2"]


def test_preview_uses_existing_exporters_effective_answer_space_policy(
    tmp_path: Path,
) -> None:
    catalog = _catalog()
    rows = catalog["papers"][0]["theme_groups"][0]["atomic_chain"]
    rows[0]["item_type"] = "embedded_single_choice"
    rows[1]["item_type"] = "reasoned_explanation"
    with running_server(build_config(tmp_path / "gateway-state")) as server:
        captured = _configure(server, catalog=catalog)
        preview = _preview(server)
        candidate = preview["candidates"][0]
        compact_rows = candidate["preview_model"]["theme_groups"][0][
            "compact_rows"
        ]
        assert [row["answer_space"]["lines"] for row in compact_rows] == [
            0,
            2,
        ]
        nested_rows = [
            row
            for printed in candidate["preview_model"]["theme_groups"][0][
                "printed_questions"
            ]
            for row in printed["atomic_rows"]
        ]
        assert [row["answer_space"]["lines"] for row in nested_rows] == [
            0,
            2,
        ]

        status, approved, _ = _approve(server, candidate)
        assert status == 200
        status, body, _ = request(
            server,
            "POST",
            "/api/v1/prep/blueprints/"
            f"{approved['data']['preview_approval_id']}/export",
            headers=_origin(server),
            payload={},
        )
        assert status == 202, body
        assert captured[0]["answer_space_lines"] == 3


def test_stale_current_snapshot_rejects_approval(tmp_path: Path) -> None:
    with running_server(build_config(tmp_path / "gateway-state")) as server:
        _configure(server)
        candidate = _preview(server)["candidates"][0]
        server.service._paper_export_scope_snapshot_id = (
            lambda principal, scope: "9" * 64
        )
        status, body, _ = _approve(server, candidate)
        assert status == 409
        assert body["error"]["code"] == "paper_blueprint_approval_stale"


def test_preview_search_collects_every_page_and_excludes_unassigned_cards(
    tmp_path: Path,
) -> None:
    with running_server(build_config(tmp_path / "gateway-state")) as server:
        captured_payloads: list[dict[str, Any]] = []
        base = {
            "schema_version": "shchem.question-search.v1",
            "scope": "master",
            "q": None,
            "filters": {},
            "data_snapshot_id": SNAPSHOT,
            "counts": {
                "theme_cards_scanned": 3,
                "theme_cards_matched": 3,
                "atomic_parts_scanned": 5,
                "atomic_parts_matched": 5,
                "returned_theme_cards": 2,
            },
            "scope_counts": {},
            "facets": {},
            "authority": {"source": "synthetic"},
            "integrity": {
                "theme_first": True,
                "atomic_matches_are_highlights_only": True,
                "complete_theme_chain_returned": False,
                "dependency_context_preserved": True,
                "unassigned_parent_not_guessed": True,
                "answer_text_excluded": True,
                "frozen_release_live_fallback_allowed": False,
            },
        }
        theme_item = {
            "scope": "master",
            "group_kind": "theme",
            "theme": {"id": "T1"},
            "counts": {"atomic_matched": 2},
        }
        pending_item = {
            "scope": "master",
            "group_kind": "unassigned_pending_review",
            "theme": None,
            "counts": {"atomic_matched": 1},
        }
        second_theme = {
            "scope": "master",
            "group_kind": "theme",
            "theme": {"id": "T2"},
            "counts": {"atomic_matched": 2},
        }

        def loader(payload: dict[str, Any]) -> dict[str, Any]:
            captured_payloads.append(deepcopy(payload))
            response = deepcopy(base)
            if "cursor" not in payload:
                response["items"] = [theme_item, pending_item]
                response["page"] = {
                    "limit": 2,
                    "returned": 2,
                    "total_theme_cards": 3,
                    "has_more": True,
                    "next_cursor": "page-two",
                }
            else:
                assert payload["cursor"] == "page-two"
                response["items"] = [second_theme]
                response["page"] = {
                    "limit": 2,
                    "returned": 1,
                    "total_theme_cards": 3,
                    "has_more": False,
                    "next_cursor": None,
                }
            return response

        result = server.service._paper_blueprint_complete_theme_search(
            loader, {"scope": "master", "limit": 2}
        )
        assert captured_payloads == [
            {"scope": "master", "limit": 2},
            {"scope": "master", "limit": 2, "cursor": "page-two"},
        ]
        assert [item["theme"]["id"] for item in result["items"]] == [
            "T1",
            "T2",
        ]
        assert result["page"] == {
            "limit": 2,
            "returned": 2,
            "total_theme_cards": 2,
            "has_more": False,
            "next_cursor": None,
            "aggregated_all_pages": True,
            "source_page_count": 2,
        }
        assert result["integrity"]["complete_theme_chain_returned"] is True


def test_openapi_strictly_validates_preview_approve_and_approved_export(
    tmp_path: Path,
) -> None:
    workspace = Path(__file__).resolve().parents[4]
    document = yaml.safe_load(
        (
            workspace
            / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
        ).read_text(encoding="utf-8")
    )
    assert document["info"]["version"] == "1.24.0"
    paths = document["paths"]
    assert {
        "/api/v1/prep/blueprints/preview",
        "/api/v1/prep/blueprints/approve",
        "/api/v1/prep/blueprints/{preview_approval_id}/export",
    } <= set(paths)
    assert "does not satisfy" in paths["/api/v1/prep/exports"]["post"][
        "description"
    ]

    def validator(name: str) -> Draft202012Validator:
        return Draft202012Validator(
            {
                "$ref": f"#/components/schemas/{name}",
                "components": document["components"],
            }
        )

    for name in (
        "PaperBlueprintPreviewRequest",
        "PaperBlueprintPreviewEnvelope",
        "PaperBlueprintApproveRequest",
        "PaperBlueprintApprovalEnvelope",
        "PaperBlueprintExportRequest",
        "PaperBlueprintApprovedExportEnvelope",
    ):
        Draft202012Validator.check_schema(document["components"]["schemas"][name])
    assert list(validator("PaperBlueprintPreviewRequest").iter_errors(_payload())) == []
    mutated = _payload()
    mutated["source_path"] = "C:/forbidden"
    assert list(validator("PaperBlueprintPreviewRequest").iter_errors(mutated))

    with running_server(build_config(tmp_path / "openapi-state")) as server:
        _configure(server)
        status, preview_body, _ = request(
            server,
            "POST",
            "/api/v1/prep/blueprints/preview",
            headers=_origin(server),
            payload=_payload(),
        )
        assert status == 200
        assert list(
            validator("PaperBlueprintPreviewEnvelope").iter_errors(preview_body)
        ) == []
        candidate = preview_body["data"]["candidates"][0]
        status, approval_body, _ = _approve(server, candidate)
        assert status == 200
        assert list(
            validator("PaperBlueprintApprovalEnvelope").iter_errors(
                approval_body
            )
        ) == []
        approval_id = approval_body["data"]["preview_approval_id"]
        status, export_body, _ = request(
            server,
            "POST",
            f"/api/v1/prep/blueprints/{approval_id}/export",
            headers=_origin(server),
            payload={},
        )
        assert status == 202
        assert list(
            validator("PaperBlueprintApprovedExportEnvelope").iter_errors(
                export_body
            )
        ) == []
