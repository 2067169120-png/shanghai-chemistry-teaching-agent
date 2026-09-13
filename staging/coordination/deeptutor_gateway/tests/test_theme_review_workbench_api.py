from __future__ import annotations

import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest

from integrations.deeptutor_shchem_v1.config import Principal, token_digest
from integrations.deeptutor_shchem_v1.public_kb import PublicKBReader
from integrations.deeptutor_shchem_v1.theme_review_workbench import (
    REVIEW_CANDIDATE_WRITE_CAPABILITY,
    REVIEW_DECISION_WRITE_CAPABILITY,
    REVIEW_TASK_WRITE_CAPABILITY,
    ThemeReviewGateway,
    ThemeReviewTaskCatalog,
)
from integrations.deeptutor_shchem_v1.theme_workbench import ThemeWorkbenchReader
from integrations.shchem_review_workbench_v1 import AppendOnlyThemeReviewStore
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    build_config,
    request,
    running_server,
)


WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"

READ_TOKEN = "theme-review-read-teacher-token-0123456789"
TASK_TOKEN = "theme-review-task-teacher-token-0123456789"
CANDIDATE_TOKEN = "theme-review-candidate-teacher-token-0123456789"
TASK_CANDIDATE_TOKEN = "theme-review-task-candidate-token-0123456789"
DECISION_TOKEN = "theme-review-decision-teacher-token-0123456789"
ALL_A_TOKEN = "theme-review-all-a-teacher-token-0123456789"
ALL_B_TOKEN = "theme-review-all-b-teacher-token-0123456789"
STUDENT_TOKEN = "theme-review-student-token-0123456789"

LIST_ROUTE = "/api/v1/review/tasks"


def _origin(server: Any) -> dict[str, str]:
    return {
        "Origin": f"http://127.0.0.1:{server.server_address[1]}",
        "Sec-Fetch-Site": "same-origin",
    }


def _data(body: dict[str, Any]) -> dict[str, Any]:
    return body["data"]


def _error_code(body: dict[str, Any]) -> str:
    return str(body["error"]["code"])


def _assert_authority_closed(authority: dict[str, Any]) -> None:
    assert authority["candidate_only"] is True
    for key, value in authority.items():
        if key != "candidate_only":
            assert value is False, key


def _assert_no_path_or_url(value: Any) -> None:
    forbidden_keys = {
        "path",
        "relative_path",
        "absolute_path",
        "source_path",
        "url",
        "uri",
        "source_url",
        "event_relative_path",
        "commit_relative_path",
    }
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = key.casefold()
            assert lowered not in forbidden_keys
            assert not lowered.endswith(("_path", "_url", "_uri"))
            _assert_no_path_or_url(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_path_or_url(child)
    elif isinstance(value, str):
        assert "://" not in value
        assert not value.startswith(("/", "\\"))
        assert not (len(value) >= 3 and value[1:3] in {":\\", ":/"})


def _review_config(tmp_path: Path):
    config = build_config(tmp_path / "gateway-state", max_request_bytes=131_072)
    all_capabilities = (
        REVIEW_TASK_WRITE_CAPABILITY,
        REVIEW_CANDIDATE_WRITE_CAPABILITY,
        REVIEW_DECISION_WRITE_CAPABILITY,
    )
    config.principals = [
        Principal("review-read", "teacher", token_digest(READ_TOKEN)),
        Principal(
            "review-task",
            "teacher",
            token_digest(TASK_TOKEN),
            capabilities=(REVIEW_TASK_WRITE_CAPABILITY,),
        ),
        Principal(
            "review-candidate",
            "teacher",
            token_digest(CANDIDATE_TOKEN),
            capabilities=(REVIEW_CANDIDATE_WRITE_CAPABILITY,),
        ),
        Principal(
            "review-task-candidate",
            "teacher",
            token_digest(TASK_CANDIDATE_TOKEN),
            capabilities=(
                REVIEW_TASK_WRITE_CAPABILITY,
                REVIEW_CANDIDATE_WRITE_CAPABILITY,
            ),
        ),
        Principal(
            "review-decision",
            "teacher",
            token_digest(DECISION_TOKEN),
            capabilities=(REVIEW_DECISION_WRITE_CAPABILITY,),
        ),
        Principal(
            "review-all-a",
            "teacher",
            token_digest(ALL_A_TOKEN),
            capabilities=all_capabilities,
        ),
        Principal(
            "review-all-b",
            "teacher",
            token_digest(ALL_B_TOKEN),
            capabilities=all_capabilities,
        ),
        Principal("review-student", "student", token_digest(STUDENT_TOKEN)),
    ]
    config.students = ()
    config.validate()
    return config


@pytest.fixture(scope="module")
def review_catalog() -> ThemeReviewTaskCatalog:
    return ThemeReviewTaskCatalog.build_live(
        PublicKBReader(SHCHEM_ROOT), ThemeWorkbenchReader(SHCHEM_ROOT)
    )


@contextmanager
def _review_server(
    tmp_path: Path, catalog: ThemeReviewTaskCatalog
) -> Iterator[tuple[Any, ThemeReviewGateway, AppendOnlyThemeReviewStore]]:
    review_workspace = tmp_path / "review-ledger"
    review_workspace.mkdir()
    store = AppendOnlyThemeReviewStore.for_test_workspace(review_workspace)
    gateway = ThemeReviewGateway(
        catalog=catalog,
        store=store,
        release_context={
            "serving_release_id": "WBREL-theme-review-api-test",
            "data_snapshot_id": "WB-DATA-theme-review-api-test",
            "browse_snapshot_id": "WB-BROWSE-theme-review-api-test",
        },
    )
    with running_server(_review_config(tmp_path)) as server:
        # The mutable owner-only ledger is injected explicitly so no request
        # can select LOCALAPPDATA, a filesystem path, or another store.
        server.service.theme_review_workbench = gateway
        yield server, gateway, store


@pytest.fixture
def api_server(tmp_path: Path, review_catalog: ThemeReviewTaskCatalog):
    with _review_server(tmp_path, review_catalog) as value:
        yield value


def _task_route(task_id: str) -> str:
    return f"{LIST_ROUTE}/{task_id}"


def _get_detail(server: Any, task_id: str, *, token: str = READ_TOKEN) -> dict[str, Any]:
    status, body, _ = request(
        server,
        "GET",
        _task_route(task_id),
        token=token,
        headers=_origin(server),
    )
    assert status == 200, body
    return _data(body)


def _claim(
    server: Any,
    task_id: str,
    *,
    token: str = ALL_A_TOKEN,
    expected_revision: str | None = None,
    key: str = "claim-theme-review-0001",
) -> tuple[int, dict[str, Any]]:
    status, body, _ = request(
        server,
        "POST",
        f"{_task_route(task_id)}/claim",
        token=token,
        payload={"expected_revision": expected_revision, "idempotency_key": key},
        headers=_origin(server),
    )
    return status, body


def _empty_change(detail: dict[str, Any], key: str) -> dict[str, Any]:
    return {
        "expected_revision": detail["revision"],
        "idempotency_key": key,
        "base_task_input_sha256": detail["base"]["task_input_sha256"],
        "tag_replacements": [],
        "hierarchy_replacements": [],
        "atomic_boundary_candidates": [],
        "dependency_replacements": [],
        "source_binding_candidates": [],
    }


def _hierarchy_change(detail: dict[str, Any], key: str) -> dict[str, Any]:
    printed = detail["printed_questions"][0]
    payload = _empty_change(detail, key)
    payload["hierarchy_replacements"] = [
        {
            "printed_question_id": printed["printed_question_id"],
            "proposed_theme_id": detail["theme_id"],
            "reason_zh": "依据冻结题图与父链证据提交整主题候选归组。",
            "evidence_ids": [printed["evidence_ids"][0]],
        }
    ]
    return payload


def _submit_change(
    server: Any,
    task_id: str,
    payload: dict[str, Any],
    *,
    token: str = ALL_A_TOKEN,
) -> tuple[int, dict[str, Any]]:
    status, body, _ = request(
        server,
        "POST",
        f"{_task_route(task_id)}/change-sets",
        token=token,
        payload=payload,
        headers=_origin(server),
    )
    return status, body


def _claim_and_change(
    server: Any,
    task_id: str,
    *,
    token: str = ALL_A_TOKEN,
    suffix: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    claimed, claim_body = _claim(
        server,
        task_id,
        token=token,
        key=f"claim-{suffix}-0001",
    )
    assert claimed == 200, claim_body
    detail = _get_detail(server, task_id, token=token)
    status, changed_body = _submit_change(
        server,
        task_id,
        _hierarchy_change(detail, f"changes-{suffix}-0001"),
        token=token,
    )
    assert status == 201, changed_body
    return _data(changed_body), _get_detail(server, task_id, token=token)


def _master_hashes() -> dict[str, str]:
    relatives = [
        PublicKBReader.MANIFEST,
        PublicKBReader.TAXONOMY,
        PublicKBReader.REVIEW_QUEUE,
        *PublicKBReader.LAYER_FILES.values(),
    ]
    return {
        relative: hashlib.sha256((SHCHEM_ROOT / relative).read_bytes()).hexdigest()
        for relative in relatives
    }


def test_teacher_reads_without_write_capability_and_students_are_denied(
    api_server,
) -> None:
    server, _, _ = api_server
    status, body, _ = request(
        server,
        "GET",
        f"{LIST_ROUTE}?limit=3&offset=0",
        token=READ_TOKEN,
        headers=_origin(server),
    )
    assert status == 200, body
    listing = _data(body)
    assert listing["count"] == 3
    assert listing["total"] == 147

    status, body, _ = request(
        server,
        "GET",
        f"{LIST_ROUTE}?limit=200&offset=0",
        token=READ_TOKEN,
        headers=_origin(server),
    )
    assert status == 200, body
    complete_listing = _data(body)
    assert complete_listing["count"] == 147
    assert complete_listing["total"] == 147
    _assert_authority_closed(listing["authority"])
    _assert_no_path_or_url(listing)

    detail = _get_detail(server, listing["items"][0]["task_id"])
    assert detail["unit_kind"] in {
        "theme_big_question",
        "paper_theme_boundary_review",
    }
    assert all(
        atom["answer_boundary"]
        == {
            "status": "reference_answer_outside_review_task",
            "official": False,
            "independently_verified": False,
        }
        for atom in detail["atomic_parts"]
    )
    _assert_authority_closed(detail["authority"])

    denied, error, _ = request(
        server,
        "GET",
        LIST_ROUTE,
        token=STUDENT_TOKEN,
        headers=_origin(server),
    )
    assert denied == 403
    assert _error_code(error) == "teacher_scope_required"


def test_three_write_capabilities_are_independent(
    api_server, review_catalog: ThemeReviewTaskCatalog
) -> None:
    server, _, _ = api_server
    task_a, task_b, task_c = review_catalog.tasks[:3]

    denied, error = _claim(server, task_a["task_id"], token=READ_TOKEN)
    assert denied == 403
    assert _error_code(error) == "review_task_write_capability_required"
    denied, error = _claim(server, task_a["task_id"], token=CANDIDATE_TOKEN)
    assert denied == 403
    assert _error_code(error) == "review_task_write_capability_required"

    claimed, body = _claim(server, task_a["task_id"], token=TASK_TOKEN)
    assert claimed == 200, body
    detail = _get_detail(server, task_a["task_id"], token=TASK_TOKEN)
    denied, error = _submit_change(
        server,
        task_a["task_id"],
        _hierarchy_change(detail, "changes-task-only-0001"),
        token=TASK_TOKEN,
    )
    assert denied == 403
    assert _error_code(error) == "review_candidate_write_capability_required"

    claimed, body = _claim(
        server,
        task_b["task_id"],
        token=TASK_CANDIDATE_TOKEN,
        key="claim-task-candidate-0001",
    )
    assert claimed == 200, body
    detail = _get_detail(server, task_b["task_id"], token=TASK_CANDIDATE_TOKEN)
    changed, change_body = _submit_change(
        server,
        task_b["task_id"],
        _hierarchy_change(detail, "changes-task-candidate-0001"),
        token=TASK_CANDIDATE_TOKEN,
    )
    assert changed == 201, change_body
    detail = _get_detail(server, task_b["task_id"], token=TASK_CANDIDATE_TOKEN)
    change_set_id = _data(change_body)["change_set_id"]
    denied, error, _ = request(
        server,
        "POST",
        f"{_task_route(task_b['task_id'])}/decisions",
        token=TASK_CANDIDATE_TOKEN,
        payload={
            "expected_revision": detail["revision"],
            "idempotency_key": "decision-task-candidate-0001",
            "change_set_id": change_set_id,
            "verdict": "blocked",
            "reason_zh": "当前证据不足，候选继续保持阻断。",
            "evidence_ids": [detail["printed_questions"][0]["evidence_ids"][0]],
        },
        headers=_origin(server),
    )
    assert denied == 403
    assert _error_code(error) == "review_decision_write_capability_required"

    denied, error = _claim(server, task_c["task_id"], token=DECISION_TOKEN)
    assert denied == 403
    assert _error_code(error) == "review_task_write_capability_required"
    changed, detail = _claim_and_change(
        server, task_c["task_id"], token=ALL_A_TOKEN, suffix="all-capabilities"
    )
    assert changed["candidate_overlay_only"] is True
    decision_status, decision_body, _ = request(
        server,
        "POST",
        f"{_task_route(task_c['task_id'])}/decisions",
        token=ALL_A_TOKEN,
        payload={
            "expected_revision": detail["revision"],
            "idempotency_key": "decision-all-capabilities-0001",
            "change_set_id": changed["change_set_id"],
            "verdict": "accept_candidate_overlay",
            "reason_zh": "确认只形成候选覆盖预览，不写中央主库。",
            "evidence_ids": [detail["printed_questions"][0]["evidence_ids"][0]],
        },
        headers=_origin(server),
    )
    assert decision_status == 201, decision_body


def test_origin_csrf_and_query_contracts_fail_before_service_mutation(
    api_server, review_catalog: ThemeReviewTaskCatalog
) -> None:
    server, _, store = api_server
    task_id = review_catalog.tasks[0]["task_id"]

    status, body, _ = request(server, "GET", LIST_ROUTE, token=READ_TOKEN)
    assert status == 403
    assert _error_code(body) == "origin_required"
    status, body, _ = request(
        server,
        "GET",
        LIST_ROUTE,
        token=READ_TOKEN,
        headers={"Origin": "https://attacker.invalid"},
    )
    assert status == 403
    assert _error_code(body) == "origin_denied"
    status, body, _ = request(
        server,
        "POST",
        f"{_task_route(task_id)}/claim",
        token=ALL_A_TOKEN,
        payload={"expected_revision": None, "idempotency_key": "claim-origin-0001"},
    )
    assert status == 403
    assert _error_code(body) == "csrf_origin_required"
    hostile = _origin(server)
    hostile["Sec-Fetch-Site"] = "cross-site"
    status, body, _ = request(
        server,
        "POST",
        f"{_task_route(task_id)}/claim",
        token=ALL_A_TOKEN,
        payload={"expected_revision": None, "idempotency_key": "claim-origin-0002"},
        headers=hostile,
    )
    assert status == 403
    assert _error_code(body) == "csrf_fetch_site_denied"

    for route in (
        f"{LIST_ROUTE}?unknown=1",
        f"{LIST_ROUTE}?limit=1&limit=2",
        f"{_task_route(task_id)}?unknown=1",
        f"{_task_route(task_id)}/change-sets/not-created/preview?unknown=",
    ):
        status, error, _ = request(
            server, "GET", route, token=READ_TOKEN, headers=_origin(server)
        )
        assert status == 400, (route, error)
        assert _error_code(error) == "theme_review_query_invalid"
        _assert_no_path_or_url(error)
    status, error, _ = request(
        server,
        "POST",
        f"{_task_route(task_id)}/claim?unknown=1",
        token=ALL_A_TOKEN,
        payload={"expected_revision": None, "idempotency_key": "claim-query-0001"},
        headers=_origin(server),
    )
    assert status == 400
    assert _error_code(error) == "theme_review_query_invalid"
    assert store.list_tasks() == []


@pytest.mark.parametrize(
    ("action", "token", "payload"),
    [
        (
            "claim",
            ALL_A_TOKEN,
            {
                "expected_revision": None,
                "idempotency_key": "claim-spoof-0001",
                "actor": "attacker",
            },
        ),
        (
            "release",
            ALL_A_TOKEN,
            {
                "expected_revision": None,
                "idempotency_key": "release-spoof-0001",
                "reason_zh": "伪造权限字段必须失败。",
                "authority": {"human_reviewed": True},
            },
        ),
        (
            "change-sets",
            ALL_A_TOKEN,
            {
                "expected_revision": None,
                "idempotency_key": "changes-spoof-0001",
                "base_task_input_sha256": "0" * 64,
                "tag_replacements": [],
                "hierarchy_replacements": [],
                "atomic_boundary_candidates": [],
                "dependency_replacements": [],
                "source_path": "C:\\secret\\master.jsonl",
            },
        ),
        (
            "decisions",
            ALL_A_TOKEN,
            {
                "expected_revision": None,
                "idempotency_key": "decision-spoof-0001",
                "change_set_id": "TRCHANGE-not-created",
                "verdict": "accept_candidate_overlay",
                "reason_zh": "浏览器不得指定复核者。",
                "evidence_ids": ["EV-not-created"],
                "reviewer_id": "forged-reviewer",
            },
        ),
    ],
)
def test_client_cannot_supply_actor_path_authority_or_reviewer(
    api_server,
    review_catalog: ThemeReviewTaskCatalog,
    action: str,
    token: str,
    payload: dict[str, Any],
) -> None:
    server, _, store = api_server
    task_id = review_catalog.tasks[0]["task_id"]
    payload = copy.deepcopy(payload)
    task_was_registered = False
    if action == "change-sets":
        claimed, claim_body = _claim(
            server,
            task_id,
            token=ALL_A_TOKEN,
            key="claim-before-spoofed-change-0001",
        )
        assert claimed == 200, claim_body
        detail = _get_detail(server, task_id)
        payload["expected_revision"] = detail["revision"]
        payload["base_task_input_sha256"] = detail["base"]["task_input_sha256"]
        task_was_registered = True
    status, body, _ = request(
        server,
        "POST",
        f"{_task_route(task_id)}/{action}",
        token=token,
        payload=payload,
        headers=_origin(server),
    )
    assert status == 400, body
    assert _error_code(body) == "theme_review_contract_invalid"
    _assert_no_path_or_url(body)
    if task_was_registered:
        assert store.list_change_sets(task_id) == []
    else:
        assert store.list_tasks() == []


def test_claim_competition_cas_and_idempotent_release(
    api_server, review_catalog: ThemeReviewTaskCatalog
) -> None:
    server, _, _ = api_server
    task_id = review_catalog.tasks[0]["task_id"]

    def compete(token: str, key: str) -> tuple[int, dict[str, Any]]:
        return _claim(server, task_id, token=token, key=key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(compete, ALL_A_TOKEN, "claim-race-a-0001"),
            pool.submit(compete, ALL_B_TOKEN, "claim-race-b-0001"),
        ]
        outcomes = [future.result(timeout=10) for future in futures]
    assert sorted(status for status, _ in outcomes) == [200, 409]
    loser_error = next(body for status, body in outcomes if status == 409)
    assert _error_code(loser_error) in {
        "theme_review_conflict",
        "theme_review_revision_conflict",
    }
    _assert_no_path_or_url(loser_error)

    detail = _get_detail(server, task_id)
    winner_id = detail["assignee"]
    winner_token = ALL_A_TOKEN if winner_id == "review-all-a" else ALL_B_TOKEN
    loser_token = ALL_B_TOKEN if winner_token == ALL_A_TOKEN else ALL_A_TOKEN
    claimed_revision = detail["revision"]
    release_payload = {
        "expected_revision": claimed_revision,
        "idempotency_key": "release-race-winner-0001",
        "reason_zh": "并发占用测试结束，释放当前任务。",
    }
    first, first_body, _ = request(
        server,
        "POST",
        f"{_task_route(task_id)}/release",
        token=winner_token,
        payload=release_payload,
        headers=_origin(server),
    )
    assert first == 200, first_body
    replay, replay_body, _ = request(
        server,
        "POST",
        f"{_task_route(task_id)}/release",
        token=winner_token,
        payload=release_payload,
        headers=_origin(server),
    )
    assert replay == 200, replay_body
    assert _data(replay_body)["idempotent_replay"] is True

    stale, stale_body = _claim(
        server,
        task_id,
        token=loser_token,
        expected_revision=claimed_revision,
        key="claim-stale-after-release-0001",
    )
    assert stale == 409
    assert _error_code(stale_body) == "theme_review_revision_conflict"


def test_only_active_assignee_can_release_change_or_decide(
    api_server, review_catalog: ThemeReviewTaskCatalog
) -> None:
    server, _, _ = api_server
    task_id = review_catalog.tasks[0]["task_id"]
    claimed, body = _claim(server, task_id, token=ALL_A_TOKEN)
    assert claimed == 200, body
    detail = _get_detail(server, task_id)

    status, error, _ = request(
        server,
        "POST",
        f"{_task_route(task_id)}/release",
        token=ALL_B_TOKEN,
        payload={
            "expected_revision": detail["revision"],
            "idempotency_key": "release-non-assignee-0001",
            "reason_zh": "非占用者不得释放任务。",
        },
        headers=_origin(server),
    )
    assert status == 403
    assert _error_code(error) == "theme_review_principal_forbidden"

    status, error = _submit_change(
        server,
        task_id,
        _hierarchy_change(detail, "changes-non-assignee-0001"),
        token=ALL_B_TOKEN,
    )
    assert status == 403
    assert _error_code(error) == "theme_review_principal_forbidden"

    changed_status, changed_body = _submit_change(
        server,
        task_id,
        _hierarchy_change(detail, "changes-assignee-0001"),
        token=ALL_A_TOKEN,
    )
    assert changed_status == 201, changed_body
    changed = _data(changed_body)
    current = _get_detail(server, task_id)
    status, error, _ = request(
        server,
        "POST",
        f"{_task_route(task_id)}/decisions",
        token=ALL_B_TOKEN,
        payload={
            "expected_revision": current["revision"],
            "idempotency_key": "decision-non-assignee-0001",
            "change_set_id": changed["change_set_id"],
            "verdict": "blocked",
            "reason_zh": "非占用者不得写教师决定。",
            "evidence_ids": [current["printed_questions"][0]["evidence_ids"][0]],
        },
        headers=_origin(server),
    )
    assert status == 403
    assert _error_code(error) == "theme_review_principal_forbidden"


def test_simplified_change_set_derives_before_and_failed_batch_is_atomic(
    api_server, review_catalog: ThemeReviewTaskCatalog
) -> None:
    server, _, store = api_server
    task = review_catalog.tasks[0]
    claimed, body = _claim(server, task["task_id"])
    assert claimed == 200, body
    detail = _get_detail(server, task["task_id"])
    atom = detail["atomic_parts"][0]
    printed = detail["printed_questions"][0]
    foreign_theme = next(
        row["theme_id"] for row in review_catalog.tasks if row["theme_id"] != detail["theme_id"]
    )

    invalid = _empty_change(detail, "changes-atomicity-invalid-0001")
    invalid["tag_replacements"] = [
        {
            "atomic_part_id": atom["atomic_part_id"],
            "changes": {"context_C": ["C01"]},
            "reason_zh": "依据题面共同情境补充候选语境标签。",
            "evidence_ids": [atom["evidence_ids"][0]],
        }
    ]
    invalid["hierarchy_replacements"] = [
        {
            "printed_question_id": printed["printed_question_id"],
            "proposed_theme_id": foreign_theme,
            "reason_zh": "跨主题归组必须使整批失败关闭。",
            "evidence_ids": [printed["evidence_ids"][0]],
        }
    ]
    status, error = _submit_change(server, task["task_id"], invalid)
    assert status == 409
    assert _error_code(error) == "theme_review_hierarchy_same_paper_violation"
    assert _get_detail(server, task["task_id"])["state"]["change_set_count"] == 0
    assert store.list_change_sets(task["task_id"]) == []

    valid = copy.deepcopy(invalid)
    valid["idempotency_key"] = "changes-atomicity-valid-0001"
    valid["hierarchy_replacements"][0]["proposed_theme_id"] = detail["theme_id"]
    status, changed_body = _submit_change(server, task["task_id"], valid)
    assert status == 201, changed_body
    changed = _data(changed_body)
    preview_status, preview_body, _ = request(
        server,
        "GET",
        f"{_task_route(task['task_id'])}/change-sets/{changed['change_set_id']}/preview",
        token=READ_TOKEN,
        headers=_origin(server),
    )
    assert preview_status == 200, preview_body
    projected = _data(preview_body)["candidate_overlay_preview"]
    assert projected["tag_replacements"][0]["before"] == atom["current_tags"]["context_C"]
    assert projected["tag_replacements"][0]["after"] == ["C01"]
    assert projected["hierarchy_replacements"][0]["before_parent_id"] is None
    assert projected["hierarchy_replacements"][0]["after_parent_id"] == detail["theme_id"]
    assert _get_detail(server, task["task_id"])["state"]["change_set_count"] == 1


def test_source_binding_fifth_change_array_is_server_frozen_and_fail_closed(
    api_server, review_catalog: ThemeReviewTaskCatalog
) -> None:
    server, _, store = api_server
    huaer = next(
        row
        for row in review_catalog.tasks
        if row["paper_id"] == "MASTER-PAPER-21b2686786eef90582ca"
        and row["source_binding_candidates"]
    )
    claimed, body = _claim(
        server,
        huaer["task_id"],
        key="claim-api-source-exact-0001",
    )
    assert claimed == 200, body
    detail = _get_detail(server, huaer["task_id"])
    candidate = detail["source_binding_candidates"][0]
    payload = _empty_change(detail, "changes-api-source-exact-0001")
    payload["source_binding_candidates"] = [
        {
            "candidate_id": candidate["candidate_id"],
            "candidate_sha256": candidate["candidate_sha256"],
            "source_version_id": candidate["source_version_id"],
            "action": "accept_binding_candidate",
            "reason_zh": "仅接受九页精确内容集所对应的来源版本候选。",
            "evidence_ids": candidate["evidence_binding_ids"],
        }
    ]
    status, body = _submit_change(server, huaer["task_id"], payload)
    assert status == 201, body
    changed = _data(body)
    preview_status, preview_body, _ = request(
        server,
        "GET",
        f"{_task_route(huaer['task_id'])}/change-sets/{changed['change_set_id']}/preview",
        token=READ_TOKEN,
        headers=_origin(server),
    )
    assert preview_status == 200, preview_body
    preview = _data(preview_body)
    source_rows = preview["candidate_overlay_preview"]["source_binding_candidates"]
    assert source_rows[0]["action"] == "accept_binding_candidate"
    assert preview["validation"]["frozen_source_binding_candidate_validated"] is True
    _assert_no_path_or_url(preview)
    current = _get_detail(server, huaer["task_id"])
    decision_status, decision_body, _ = request(
        server,
        "POST",
        f"{_task_route(huaer['task_id'])}/decisions",
        token=ALL_A_TOKEN,
        payload={
            "expected_revision": current["revision"],
            "idempotency_key": "decision-api-source-exact-0001",
            "change_set_id": changed["change_set_id"],
            "verdict": "accept_candidate_overlay",
            "reason_zh": "确认仅记录卷级来源候选决定，不写中央主库。",
            "evidence_ids": candidate["evidence_binding_ids"],
        },
        headers=_origin(server),
    )
    assert decision_status == 201, decision_body
    assert _data(decision_body)["verdict"] == "accept_candidate_overlay"
    decided_status, decided_body, _ = request(
        server,
        "GET",
        f"{_task_route(huaer['task_id'])}/change-sets/{changed['change_set_id']}/preview",
        token=READ_TOKEN,
        headers=_origin(server),
    )
    assert decided_status == 200, decided_body
    decided = _data(decided_body)["candidate_overlay_preview"]
    assert decided["verdict"] == "accept_candidate_overlay"
    assert decided["source_binding_candidates"][0]["action"] == (
        "accept_binding_candidate"
    )

    blocked = next(
        row
        for row in review_catalog.tasks
        if row["paper_id"] == "MASTER-PAPER-4a39a5ecb376c90f8ed7"
        and row["source_binding_candidates"]
    )
    claimed, body = _claim(
        server,
        blocked["task_id"],
        key="claim-api-source-blocked-0001",
    )
    assert claimed == 200, body
    blocked_detail = _get_detail(server, blocked["task_id"])
    blocked_candidate = blocked_detail["source_binding_candidates"][0]
    rejected = _empty_change(
        blocked_detail, "changes-api-source-blocked-0001"
    )
    rejected["source_binding_candidates"] = [
        {
            "candidate_id": blocked_candidate["candidate_id"],
            "candidate_sha256": blocked_candidate["candidate_sha256"],
            "source_version_id": blocked_candidate["source_version_id"],
            "action": "accept_binding_candidate",
            "reason_zh": "没有中央来源记录时不得接受。",
            "evidence_ids": blocked_candidate["evidence_binding_ids"],
        }
    ]
    status, error = _submit_change(server, blocked["task_id"], rejected)
    assert status == 409
    assert _error_code(error) == "theme_review_source_binding_blocked"
    assert store.list_change_sets(blocked["task_id"]) == []

    smuggled = copy.deepcopy(rejected)
    smuggled["idempotency_key"] = "changes-api-source-smuggle-0001"
    smuggled["source_binding_candidates"][0]["action"] = (
        "request_source_evidence"
    )
    smuggled["source_binding_candidates"][0]["source_id"] = "file-attacker"
    status, error = _submit_change(server, blocked["task_id"], smuggled)
    assert status == 400
    assert _error_code(error) == "theme_review_contract_invalid"
    assert store.list_change_sets(blocked["task_id"]) == []


def test_cross_theme_backward_and_cycle_attempts_never_append(
    api_server, review_catalog: ThemeReviewTaskCatalog
) -> None:
    server, _, store = api_server
    task = review_catalog.tasks[0]
    claimed, body = _claim(server, task["task_id"])
    assert claimed == 200, body
    detail = _get_detail(server, task["task_id"])
    atoms = detail["atomic_parts"]
    typed_atoms = [
        atom for atom in atoms if atom["dependency"]["dependencies"] is not None
    ]
    assert len(typed_atoms) >= 3
    first, second, last = typed_atoms[0], typed_atoms[1], typed_atoms[-1]
    foreign_atomic = next(
        row["target_atomic_part_ids"][0]
        for row in review_catalog.tasks[1:]
        if row["target_atomic_part_ids"]
        and row["target_atomic_part_ids"][0]
        not in {atom["atomic_part_id"] for atom in atoms}
    )

    attempts = []
    cross_theme = _empty_change(detail, "changes-cross-theme-edge-0001")
    cross_theme["dependency_replacements"] = [
        {
            "dependent_atomic_part_id": last["atomic_part_id"],
            "prior_atomic_part_ids": [foreign_atomic],
            "relationship_kinds": ["uses_prior_answer"],
            "evidence_ids": [last["evidence_ids"][0]],
            "reason_zh": "跨主题 atomic 依赖必须失败关闭。",
        }
    ]
    attempts.append(cross_theme)

    backward = _empty_change(detail, "changes-backward-edge-0001")
    backward["dependency_replacements"] = [
        {
            "dependent_atomic_part_id": first["atomic_part_id"],
            "prior_atomic_part_ids": [last["atomic_part_id"]],
            "relationship_kinds": ["uses_prior_answer"],
            "evidence_ids": [first["evidence_ids"][0]],
            "reason_zh": "后题不得成为前题的依赖来源。",
        }
    ]
    attempts.append(backward)

    cycle = _empty_change(detail, "changes-cycle-edge-0001")
    cycle["dependency_replacements"] = [
        {
            "dependent_atomic_part_id": first["atomic_part_id"],
            "prior_atomic_part_ids": [second["atomic_part_id"]],
            "relationship_kinds": ["uses_prior_answer"],
            "evidence_ids": [first["evidence_ids"][0]],
            "reason_zh": "两节点环中的后向边必须先失败。",
        },
        {
            "dependent_atomic_part_id": second["atomic_part_id"],
            "prior_atomic_part_ids": [first["atomic_part_id"]],
            "relationship_kinds": ["uses_prior_answer"],
            "evidence_ids": [second["evidence_ids"][0]],
            "reason_zh": "两节点环不得写入候选账本。",
        },
    ]
    attempts.append(cycle)

    for payload in attempts:
        status, error = _submit_change(server, task["task_id"], payload)
        assert status == 409, error
        assert _error_code(error) in {
            "theme_review_dependency_not_strictly_forward",
            "theme_review_dependency_cycle",
        }
        _assert_no_path_or_url(error)
    assert store.list_change_sets(task["task_id"]) == []
    assert _get_detail(server, task["task_id"])["state"]["change_set_count"] == 0


def test_preview_is_candidate_only_and_central_master_bytes_do_not_change(
    api_server, review_catalog: ThemeReviewTaskCatalog
) -> None:
    server, _, _ = api_server
    before = _master_hashes()
    task_id = review_catalog.tasks[0]["task_id"]
    changed, _ = _claim_and_change(server, task_id, suffix="preview-authority")
    status, body, _ = request(
        server,
        "GET",
        f"{_task_route(task_id)}/change-sets/{changed['change_set_id']}/preview",
        token=READ_TOKEN,
        headers=_origin(server),
    )
    assert status == 200, body
    preview = _data(body)
    _assert_authority_closed(preview["authority"])
    assert preview["candidate_overlay_preview"]["eligible_for_central_apply"] is False
    assert preview["validation"] == {
        "valid": True,
        "status": "pass",
        "task_catalog_binding_current": True,
        "same_paper_hierarchy_validated": True,
        "controlled_tag_vocabulary_validated": True,
        "strict_forward_dependency_graph_validated": True,
        "frozen_source_binding_candidate_validated": True,
        "central_apply_available": False,
    }
    _assert_no_path_or_url(preview)
    assert _master_hashes() == before


@pytest.mark.parametrize(
    ("verdict", "overlay_status"),
    [
        ("accept_candidate_overlay", "accepted_candidate_overlay_only"),
        ("reject", "rejected"),
        ("request_changes", "changes_requested"),
        ("blocked", "blocked"),
    ],
)
def test_all_four_decisions_are_immutable_candidate_records(
    tmp_path: Path,
    review_catalog: ThemeReviewTaskCatalog,
    verdict: str,
    overlay_status: str,
) -> None:
    # Each verdict gets its own owner-only ledger so the four branches are
    # independent and can share the same frozen task identity.
    with _review_server(tmp_path, review_catalog) as (server, _, store):
        before = _master_hashes()
        task_id = review_catalog.tasks[0]["task_id"]
        suffix = verdict.replace("_", "-")
        changed, detail = _claim_and_change(server, task_id, suffix=suffix)
        evidence_id = detail["printed_questions"][0]["evidence_ids"][0]
        decision_payload = {
            "expected_revision": detail["revision"],
            "idempotency_key": f"decision-{suffix}-0001",
            "change_set_id": changed["change_set_id"],
            "verdict": verdict,
            "reason_zh": "教师决定仅记录在候选复核账本，不修改中央主库。",
            "evidence_ids": [evidence_id],
        }
        status, body, _ = request(
            server,
            "POST",
            f"{_task_route(task_id)}/decisions",
            token=ALL_A_TOKEN,
            payload=decision_payload,
            headers=_origin(server),
        )
        assert status == 201, body
        decision = _data(body)
        assert decision["verdict"] == verdict
        assert decision["candidate_overlay_only"] is True
        _assert_authority_closed(decision["authority"])

        replay_status, replay_body, _ = request(
            server,
            "POST",
            f"{_task_route(task_id)}/decisions",
            token=ALL_A_TOKEN,
            payload=decision_payload,
            headers=_origin(server),
        )
        assert replay_status == 201, replay_body
        assert _data(replay_body)["idempotent_replay"] is True

        current = _get_detail(server, task_id)
        duplicate = copy.deepcopy(decision_payload)
        duplicate["expected_revision"] = current["revision"]
        duplicate["idempotency_key"] = f"decision-{suffix}-0002"
        duplicate["reason_zh"] = "同一 change-set 不得写入第二个不可变决定。"
        conflict, conflict_body, _ = request(
            server,
            "POST",
            f"{_task_route(task_id)}/decisions",
            token=ALL_A_TOKEN,
            payload=duplicate,
            headers=_origin(server),
        )
        assert conflict == 409, conflict_body
        assert _error_code(conflict_body) == "theme_review_conflict"
        _assert_no_path_or_url(conflict_body)

        preview_status, preview_body, _ = request(
            server,
            "GET",
            f"{_task_route(task_id)}/change-sets/{changed['change_set_id']}/preview",
            token=READ_TOKEN,
            headers=_origin(server),
        )
        assert preview_status == 200, preview_body
        preview = _data(preview_body)
        assert preview["candidate_overlay_preview"]["verdict"] == verdict
        assert preview["candidate_overlay_preview"]["overlay_status"] == overlay_status
        _assert_authority_closed(preview["authority"])
        assert len(store.list_decisions(task_id)) == 1
        assert _master_hashes() == before
