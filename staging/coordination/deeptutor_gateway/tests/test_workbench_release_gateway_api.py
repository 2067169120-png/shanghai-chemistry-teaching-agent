from __future__ import annotations

import hashlib
import os
import re
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from integrations.deeptutor_shchem_v1 import workbench_release_gateway as gateway_module
from integrations.deeptutor_shchem_v1.candidate_review import CandidateCropPayload
from integrations.deeptutor_shchem_v1.config import Principal, token_digest
from integrations.deeptutor_shchem_v1.workbench_release_gateway import (
    WORKBENCH_RELEASE_ACTIVATE_CAPABILITY,
    WORKBENCH_RELEASE_PREPARE_CAPABILITY,
    WorkbenchReleaseGateway,
    WorkbenchReleaseGatewayError,
)
from integrations.deeptutor_shchem_v1.workbench_release_snapshot import (
    FrozenBrowsePayload,
    FrozenWorkbenchBrowseReader,
    WorkbenchReleaseSnapshotError,
)
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    build_config,
    request,
    running_server,
)

WORKSPACE = Path(__file__).resolve().parents[4]
CANDIDATE_ROOT_ENV = "SHCHEM_WORKBENCH_RELEASE_CANDIDATE_ROOT"

READ_TOKEN = "release-api-read-teacher-token-0123456789"
PREPARE_TOKEN = "release-api-prepare-teacher-token-0123456789"
ACTIVATE_TOKEN = "release-api-activate-teacher-token-0123456789"
BOTH_TOKEN = "release-api-both-teacher-token-0123456789"
NO_CAP_TOKEN = "release-api-no-cap-teacher-token-0123456789"
STUDENT_TOKEN = "release-api-student-token-0123456789"

RELEASE_A = "WBREL-" + "a" * 64
RELEASE_B = "WBREL-" + "b" * 64
RUN_A = "WBRUN-" + "c" * 32
RUN_B = "WBRUN-" + "d" * 32
REVISION = "WBREV-" + "e" * 32
UNKNOWN_RUN = "WBRUN-" + "f" * 32


class _PendingReservationFuture:
    def done(self) -> bool:
        return False


class _RecordingExecutor:
    def __init__(self) -> None:
        self.shutdown_calls: list[tuple[bool, bool]] = []

    def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
        self.shutdown_calls.append((wait, cancel_futures))


def test_reservation_wait_accepts_real_run_id_after_old_five_second_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"seconds": 0.0}
    gateway = object.__new__(WorkbenchReleaseGateway)
    gateway._future = _PendingReservationFuture()
    gateway._cancel_event = threading.Event()
    gateway._lock = threading.RLock()

    def status() -> dict[str, Any]:
        value: dict[str, Any] = {
            "release_id": RELEASE_A,
            "state": "running",
        }
        if clock["seconds"] >= 6.0:
            value["run_id"] = RUN_A
        return value

    gateway._memory_run_status = status  # type: ignore[method-assign]
    monkeypatch.setattr(
        gateway_module.time, "monotonic", lambda: clock["seconds"]
    )
    monkeypatch.setattr(
        gateway_module.time,
        "sleep",
        lambda seconds: clock.__setitem__("seconds", clock["seconds"] + seconds),
    )
    monkeypatch.setattr(
        gateway_module, "REGRESSION_RESERVATION_WAIT_SECONDS", 60.0
    )
    monkeypatch.setattr(
        gateway_module, "REGRESSION_RESERVATION_POLL_SECONDS", 1.0
    )

    run = gateway._wait_for_committed_regression_reservation()
    assert clock["seconds"] == 6.0
    assert run["run_id"] == RUN_A
    assert gateway._cancel_event.is_set() is False


def test_reservation_wait_timeout_fails_closed_and_shutdown_joins_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"seconds": 0.0}
    gateway = object.__new__(WorkbenchReleaseGateway)
    gateway._future = _PendingReservationFuture()
    gateway._cancel_event = threading.Event()
    gateway._lock = threading.RLock()
    gateway._closed = False
    executor = _RecordingExecutor()
    gateway._executor = executor
    gateway._memory_run_status = lambda: {  # type: ignore[method-assign]
        "release_id": RELEASE_A,
        "state": "running",
    }
    monkeypatch.setattr(
        gateway_module.time, "monotonic", lambda: clock["seconds"]
    )
    monkeypatch.setattr(
        gateway_module.time,
        "sleep",
        lambda seconds: clock.__setitem__("seconds", clock["seconds"] + seconds),
    )
    monkeypatch.setattr(
        gateway_module, "REGRESSION_RESERVATION_WAIT_SECONDS", 8.0
    )
    monkeypatch.setattr(
        gateway_module, "REGRESSION_RESERVATION_POLL_SECONDS", 1.0
    )

    with pytest.raises(WorkbenchReleaseGatewayError) as captured:
        gateway._wait_for_committed_regression_reservation()
    assert captured.value.code == "workbench_release_regression_reservation_unavailable"
    assert captured.value.status == 503
    assert clock["seconds"] == 8.0
    assert gateway._cancel_event.is_set() is True

    gateway.shutdown()
    assert gateway._closed is True
    assert executor.shutdown_calls == [(True, False)]


def _origin(server: Any) -> dict[str, str]:
    return {
        "Origin": f"http://127.0.0.1:{server.server_address[1]}",
        "Sec-Fetch-Site": "same-origin",
    }


def _data(body: dict[str, Any]) -> dict[str, Any]:
    return body["data"]


def _error_code(body: dict[str, Any]) -> str:
    return str(body["error"]["code"])


class _FakeReleaseGateway:
    """Small in-memory authority double; it never invokes the real runner."""

    def __init__(self) -> None:
        self.freeze_calls = 0
        self.start_calls: list[dict[str, Any]] = []
        self.cancel_calls: list[dict[str, Any]] = []
        self.selection_calls: list[dict[str, Any]] = []
        self.ledger = {RUN_A: RELEASE_A, RUN_B: RELEASE_B}
        self.closed = False

    @staticmethod
    def _run(run_id: str, release_id: str) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "receipt_id": "WBRR-" + run_id.removeprefix("WBRUN-"),
            "release_id": release_id,
            "state": "completed",
            "verdict": "PASS",
            "trusted_pass_current": True,
            "cancellable": False,
            "automatic_activation": False,
        }

    def status(self) -> dict[str, Any]:
        return {
            "schema_version": "shchem.workbench.release_gateway.v1",
            "serving_release": {"mode": "bootstrap_live", "release_id": None},
            "selected_release": None,
            "selected_release_trust": {
                "state": "none",
                "current_trusted": False,
                "replacement_trusted": False,
                "serving_eligible": False,
                "rollback_eligible": False,
                "replacement_only": False,
            },
            "restart_required": False,
            "selection_effect_policy": "next_clean_restart_only",
            "active_regression": None,
            "automatic_activation": False,
            "client_supplied_commands_allowed": False,
            "candidate_only": True,
            "human_reviewed": False,
            "teaching_use_allowed": False,
            "publication_allowed": False,
        }

    def list_candidates(self) -> dict[str, Any]:
        return {
            "items": [
                {
                    "release_id": RELEASE_A,
                    "candidate_only": True,
                    "regression": self._run(RUN_A, RELEASE_A),
                }
            ],
            "count": 1,
            "candidate_only": True,
            "automatic_activation": False,
        }

    def freeze_candidate(self) -> dict[str, Any]:
        self.freeze_calls += 1
        return {
            "candidate": {
                "release_id": RELEASE_A,
                "candidate_only": True,
                "human_reviewed": False,
                "teaching_use_allowed": False,
                "publication_allowed": False,
            },
            "regression": {"release_id": RELEASE_A, "state": "not_started"},
            "selection_committed": False,
            "serving_changed": False,
            "automatic_activation": False,
        }

    def start_regression(
        self, release_id: str, *, idempotency_key: str
    ) -> dict[str, Any]:
        self.start_calls.append(
            {"release_id": release_id, "idempotency_key": idempotency_key}
        )
        return {
            "run": self._run(RUN_A, release_id),
            "idempotent": False,
            "_http_status": 202,
        }

    def regression_status(self, release_id: str, run_id: str) -> dict[str, Any]:
        if self.ledger.get(run_id) != release_id:
            raise WorkbenchReleaseGatewayError(
                "fake_release_run_not_found", "run is not in the trusted ledger", 404
            )
        return {"run": self._run(run_id, release_id)}

    def cancel_regression(self, release_id: str, run_id: str) -> dict[str, Any]:
        self.cancel_calls.append({"release_id": release_id, "run_id": run_id})
        return {
            "run": {
                **self._run(run_id, release_id),
                "cancellation_requested": True,
            },
            "automatic_activation": False,
        }

    def _select(
        self,
        release_id: str,
        run_id: str,
        *,
        action: str,
        expected_revision: str | None,
        principal_id: str,
        reason_zh: str,
    ) -> dict[str, Any]:
        # This lookup models the production owner-only receipt ledger.  The
        # HTTP caller supplies only run_id and can never inject receipt bytes.
        if self.ledger.get(run_id) != release_id:
            raise WorkbenchReleaseGatewayError(
                "fake_untrusted_release_run",
                "run is absent from the trusted server ledger",
                409,
            )
        self.selection_calls.append(
            {
                "release_id": release_id,
                "run_id": run_id,
                "action": action,
                "expected_revision": expected_revision,
                "principal_id": principal_id,
                "reason_zh": reason_zh,
            }
        )
        return {
            "selected_release": {
                "release_id": release_id,
                "revision": REVISION,
                "action": action,
            },
            "selection_committed": True,
            "serving_changed": False,
            "restart_required": True,
            "effect_message_zh": "版本选择已提交；完成一次安全停止并重新启动后生效。",
            "candidate_only": True,
            "human_reviewed": False,
            "teaching_use_allowed": False,
            "publication_allowed": False,
        }

    def select_release(
        self,
        release_id: str,
        run_id: str,
        *,
        expected_revision: str | None,
        principal_id: str,
        reason_zh: str,
    ) -> dict[str, Any]:
        return self._select(
            release_id,
            run_id,
            action="activate",
            expected_revision=expected_revision,
            principal_id=principal_id,
            reason_zh=reason_zh,
        )

    def rollback_release(
        self,
        release_id: str,
        run_id: str,
        *,
        expected_revision: str,
        principal_id: str,
        reason_zh: str,
    ) -> dict[str, Any]:
        return self._select(
            release_id,
            run_id,
            action="rollback",
            expected_revision=expected_revision,
            principal_id=principal_id,
            reason_zh=reason_zh,
        )

    def shutdown(self) -> None:
        self.closed = True


def _release_config(tmp_path: Path):
    config = build_config(tmp_path / "gateway-state")
    config.principals = [
        Principal("release-read", "teacher", token_digest(READ_TOKEN)),
        Principal(
            "release-prepare",
            "teacher",
            token_digest(PREPARE_TOKEN),
            capabilities=(WORKBENCH_RELEASE_PREPARE_CAPABILITY,),
        ),
        Principal(
            "release-activate",
            "teacher",
            token_digest(ACTIVATE_TOKEN),
            capabilities=(WORKBENCH_RELEASE_ACTIVATE_CAPABILITY,),
        ),
        Principal(
            "release-both",
            "teacher",
            token_digest(BOTH_TOKEN),
            capabilities=(
                WORKBENCH_RELEASE_PREPARE_CAPABILITY,
                WORKBENCH_RELEASE_ACTIVATE_CAPABILITY,
            ),
        ),
        Principal("release-no-cap", "teacher", token_digest(NO_CAP_TOKEN)),
        Principal("release-student", "student", token_digest(STUDENT_TOKEN)),
    ]
    config.students = ()
    config.validate()
    return config


@contextmanager
def _release_server(tmp_path: Path):
    fake = _FakeReleaseGateway()
    with running_server(_release_config(tmp_path)) as server:
        server.service.workbench_release_gateway = fake
        yield server, fake


def test_teacher_read_routes_need_no_write_capability_but_students_are_denied(
    tmp_path: Path,
) -> None:
    with _release_server(tmp_path) as (server, _):
        headers = _origin(server)
        routes = (
            "/api/v1/workbench/releases/status",
            "/api/v1/workbench/releases/candidates",
            f"/api/v1/workbench/releases/{RELEASE_A}/regressions/{RUN_A}",
        )
        for route in routes:
            status, body, _ = request(
                server, "GET", route, token=READ_TOKEN, headers=headers
            )
            assert status == 200, body
            assert "data" in body
            denied, error, _ = request(
                server, "GET", route, token=STUDENT_TOKEN, headers=headers
            )
            assert denied == 403
            assert _error_code(error) == "teacher_scope_required"


def test_prepare_and_activate_capabilities_are_independent(tmp_path: Path) -> None:
    with _release_server(tmp_path) as (server, fake):
        headers = _origin(server)
        prepared, body, _ = request(
            server,
            "POST",
            "/api/v1/workbench/releases/candidates",
            token=PREPARE_TOKEN,
            payload={},
            headers=headers,
        )
        assert prepared == 201, body
        assert fake.freeze_calls == 1
        denied, error, _ = request(
            server,
            "POST",
            "/api/v1/workbench/releases/candidates",
            token=ACTIVATE_TOKEN,
            payload={},
            headers=headers,
        )
        assert denied == 403
        assert _error_code(error) == "workbench_release_prepare_capability_required"

        selection = {
            "run_id": RUN_A,
            "expected_revision": None,
            "reason_zh": "教师确认切换候选浏览版本",
        }
        selected, selected_body, _ = request(
            server,
            "POST",
            f"/api/v1/workbench/releases/{RELEASE_A}/select",
            token=ACTIVATE_TOKEN,
            payload=selection,
            headers=headers,
        )
        assert selected == 200, selected_body
        denied, error, _ = request(
            server,
            "POST",
            f"/api/v1/workbench/releases/{RELEASE_A}/select",
            token=PREPARE_TOKEN,
            payload=selection,
            headers=headers,
        )
        assert denied == 403
        assert _error_code(error) == "workbench_release_activate_capability_required"


@pytest.mark.parametrize(
    ("route", "token", "payload"),
    [
        ("/api/v1/workbench/releases/candidates", PREPARE_TOKEN, {}),
        (
            f"/api/v1/workbench/releases/{RELEASE_A}/regressions",
            PREPARE_TOKEN,
            {"idempotency_key": "fixed-release-api-001"},
        ),
        (
            f"/api/v1/workbench/releases/{RELEASE_A}/regressions/{RUN_A}/cancel",
            PREPARE_TOKEN,
            {},
        ),
        (
            f"/api/v1/workbench/releases/{RELEASE_A}/select",
            ACTIVATE_TOKEN,
            {
                "run_id": RUN_A,
                "expected_revision": None,
                "reason_zh": "教师确认切换候选版本",
            },
        ),
        (
            f"/api/v1/workbench/releases/{RELEASE_B}/rollback",
            ACTIVATE_TOKEN,
            {
                "run_id": RUN_B,
                "expected_revision": REVISION,
                "reason_zh": "当前版本异常，回退历史候选版本",
            },
        ),
    ],
)
def test_every_release_post_requires_a_trusted_same_origin(
    tmp_path: Path, route: str, token: str, payload: dict[str, Any]
) -> None:
    with _release_server(tmp_path) as (server, _):
        missing, missing_body, _ = request(
            server, "POST", route, token=token, payload=payload
        )
        assert missing == 403
        assert _error_code(missing_body) == "csrf_origin_required"
        evil, evil_body, _ = request(
            server,
            "POST",
            route,
            token=token,
            payload=payload,
            headers={
                "Origin": "https://evil.example",
                "Sec-Fetch-Site": "cross-site",
            },
        )
        assert evil == 403
        assert _error_code(evil_body) in {"origin_denied", "csrf_fetch_site_denied"}


@pytest.mark.parametrize("forbidden", ["command", "path", "receipt"])
def test_freeze_and_fixed_regression_reject_client_execution_material(
    tmp_path: Path, forbidden: str
) -> None:
    with _release_server(tmp_path) as (server, fake):
        headers = _origin(server)
        status, body, _ = request(
            server,
            "POST",
            "/api/v1/workbench/releases/candidates",
            token=PREPARE_TOKEN,
            payload={forbidden: "client-controlled"},
            headers=headers,
        )
        assert status == 400
        assert _error_code(body) == "workbench_release_freeze_request_invalid"
        assert fake.freeze_calls == 0

        regression_payload = {
            "idempotency_key": "fixed-release-api-001",
            forbidden: "client-controlled",
        }
        status, body, _ = request(
            server,
            "POST",
            f"/api/v1/workbench/releases/{RELEASE_A}/regressions",
            token=PREPARE_TOKEN,
            payload=regression_payload,
            headers=headers,
        )
        assert status == 400
        assert _error_code(body) == "workbench_release_regression_request_invalid"
        assert fake.start_calls == []

        accepted, accepted_body, _ = request(
            server,
            "POST",
            f"/api/v1/workbench/releases/{RELEASE_A}/regressions",
            token=PREPARE_TOKEN,
            payload={"idempotency_key": "fixed-release-api-001"},
            headers=headers,
        )
        assert accepted == 202, accepted_body
        assert fake.start_calls == [
            {
                "release_id": RELEASE_A,
                "idempotency_key": "fixed-release-api-001",
            }
        ]


@pytest.mark.parametrize("forbidden", ["command", "path", "receipt"])
def test_selection_rejects_client_receipts_commands_and_paths(
    tmp_path: Path, forbidden: str
) -> None:
    with _release_server(tmp_path) as (server, fake):
        payload = {
            "run_id": RUN_A,
            "expected_revision": None,
            "reason_zh": "教师确认切换候选版本",
            forbidden: "client-controlled",
        }
        status, body, _ = request(
            server,
            "POST",
            f"/api/v1/workbench/releases/{RELEASE_A}/select",
            token=ACTIVATE_TOKEN,
            payload=payload,
            headers=_origin(server),
        )
        assert status == 400
        assert _error_code(body) == "workbench_release_selection_request_invalid"
        assert fake.selection_calls == []


def test_select_and_rollback_resolve_server_ledger_and_require_restart(
    tmp_path: Path,
) -> None:
    with _release_server(tmp_path) as (server, fake):
        headers = _origin(server)
        select_payload = {
            "run_id": RUN_A,
            "expected_revision": None,
            "reason_zh": "教师确认切换候选浏览版本",
        }
        status, body, _ = request(
            server,
            "POST",
            f"/api/v1/workbench/releases/{RELEASE_A}/select",
            token=ACTIVATE_TOKEN,
            payload=select_payload,
            headers=headers,
        )
        assert status == 200, body
        selected = _data(body)
        assert selected["selection_committed"] is True
        assert selected["serving_changed"] is False
        assert selected["restart_required"] is True
        assert selected["teaching_use_allowed"] is False
        assert selected["publication_allowed"] is False
        assert fake.selection_calls[-1] == {
            "release_id": RELEASE_A,
            "run_id": RUN_A,
            "action": "activate",
            "expected_revision": None,
            "principal_id": "release-activate",
            "reason_zh": "教师确认切换候选浏览版本",
        }

        unknown = {**select_payload, "run_id": UNKNOWN_RUN}
        status, body, _ = request(
            server,
            "POST",
            f"/api/v1/workbench/releases/{RELEASE_A}/select",
            token=ACTIVATE_TOKEN,
            payload=unknown,
            headers=headers,
        )
        assert status == 409
        assert _error_code(body) == "fake_untrusted_release_run"

        for invalid_reason in ("  ", "machine pass only"):
            invalid = {**select_payload, "reason_zh": invalid_reason}
            status, body, _ = request(
                server,
                "POST",
                f"/api/v1/workbench/releases/{RELEASE_A}/select",
                token=ACTIVATE_TOKEN,
                payload=invalid,
                headers=headers,
            )
            assert status == 400
            assert _error_code(body) == "workbench_release_selection_request_invalid"

        rollback_payload = {
            "run_id": RUN_B,
            "expected_revision": REVISION,
            "reason_zh": "当前版本异常，回退历史候选版本",
        }
        status, body, _ = request(
            server,
            "POST",
            f"/api/v1/workbench/releases/{RELEASE_B}/rollback",
            token=ACTIVATE_TOKEN,
            payload=rollback_payload,
            headers=headers,
        )
        assert status == 200, body
        rolled_back = _data(body)
        assert rolled_back["serving_changed"] is False
        assert rolled_back["restart_required"] is True
        assert fake.selection_calls[-1]["action"] == "rollback"
        assert fake.selection_calls[-1]["run_id"] == RUN_B


@pytest.mark.parametrize("token", [NO_CAP_TOKEN, STUDENT_TOKEN])
def test_no_capability_teacher_and_student_cannot_mutate_releases(
    tmp_path: Path, token: str
) -> None:
    with _release_server(tmp_path) as (server, _):
        headers = _origin(server)
        prepare_status, prepare_body, _ = request(
            server,
            "POST",
            "/api/v1/workbench/releases/candidates",
            token=token,
            payload={},
            headers=headers,
        )
        assert prepare_status == 403
        assert _error_code(prepare_body) in {
            "teacher_scope_required",
            "workbench_release_prepare_capability_required",
        }
        activate_status, activate_body, _ = request(
            server,
            "POST",
            f"/api/v1/workbench/releases/{RELEASE_A}/select",
            token=token,
            payload={
                "run_id": RUN_A,
                "expected_revision": None,
                "reason_zh": "教师确认切换候选版本",
            },
            headers=headers,
        )
        assert activate_status == 403
        assert _error_code(activate_body) in {
            "teacher_scope_required",
            "workbench_release_activate_capability_required",
        }


class _NoLiveFallback:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"live reader fallback was attempted: {name}")


class _FakeFrozenReader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def static(self, relative: str) -> FrozenBrowsePayload:
        self.calls.append(("static", relative))
        if relative == "styles.css":
            raise WorkbenchReleaseSnapshotError(
                "fake_frozen_static_missing", "frozen static is missing", 404
            )
        raw = b"FROZEN-APP-JS"
        return FrozenBrowsePayload(
            data=raw,
            sha256=hashlib.sha256(raw).hexdigest(),
            content_type="text/javascript; charset=utf-8",
        )

    def theme_groups(self, scope: str) -> dict[str, Any]:
        self.calls.append(("theme", scope))
        return {"source": "frozen-memory", "scope": scope, "items": []}

    def json(self, route: str) -> dict[str, Any]:
        self.calls.append(("json", route))
        return {"source": "frozen-memory", "route": route}

    def crop(self, route: str) -> CandidateCropPayload:
        self.calls.append(("crop", route))
        raw = b"\x89PNG\r\n\x1a\nFROZEN-CROP"
        return CandidateCropPayload(
            data=raw,
            sha256=hashlib.sha256(raw).hexdigest(),
            content_type="image/png",
        )


def _install_frozen_only(server: Any, reader: Any) -> None:
    server.service.frozen_browse = reader
    server.service.theme_workbench = _NoLiveFallback()
    server.service.workbench_product_registry = _NoLiveFallback()
    server.service.master_wave1_workbench = _NoLiveFallback()
    server.service.candidate_review = _NoLiveFallback()


def test_frozen_static_theme_detail_and_crop_are_memory_only_without_live_fallback(
    tmp_path: Path,
) -> None:
    config = _release_config(tmp_path)
    with running_server(config) as server:
        reader = _FakeFrozenReader()
        _install_frozen_only(server, reader)
        headers = _origin(server)

        status, raw, _ = request(server, "GET", "/overlay/app.js", token=None)
        assert status == 200
        assert raw == b"FROZEN-APP-JS"
        # The live overlay contains styles.css, so this 404 proves the frozen
        # miss did not fall back to live workspace bytes.
        status, body, _ = request(server, "GET", "/overlay/styles.css", token=None)
        assert status == 404
        assert _error_code(body) == "fake_frozen_static_missing"

        status, body, _ = request(
            server,
            "GET",
            "/api/v1/kb/workbench/theme-groups?scope=master",
            token=READ_TOKEN,
            headers=headers,
        )
        assert status == 200
        assert _data(body)["source"] == "frozen-memory"
        assert _data(body)["scope"] == "master"

        detail_route = "/api/v1/kb/workbench/master-atomic/FROZEN-NODE"
        status, body, _ = request(
            server, "GET", detail_route, token=READ_TOKEN, headers=headers
        )
        assert status == 200
        assert _data(body) == {"source": "frozen-memory", "route": detail_route}

        crop_route = (
            "/api/v1/kb/sources/candidate_review_only/wave1/nodes/"
            "atomic_part/FROZEN-NODE/question-crops/FROZEN-CROP"
        )
        status, crop, _ = request(
            server, "GET", crop_route, token=READ_TOKEN, headers=headers
        )
        assert status == 200
        assert crop == b"\x89PNG\r\n\x1a\nFROZEN-CROP"
        assert ("theme", "master") in reader.calls
        assert ("json", detail_route) in reader.calls
        assert ("crop", crop_route) in reader.calls


def _required_candidate_root() -> Path:
    exact_node_selected = any(
        "::test_fixed_runner_candidate_frozen_http_smoke" in argument
        for argument in sys.argv[1:]
    )
    if not exact_node_selected:
        pytest.skip("fixed candidate root is injected only by the release runner")
    value = os.environ.get(CANDIDATE_ROOT_ENV)
    if not value:
        pytest.fail(f"fixed candidate smoke requires runner-owned {CANDIDATE_ROOT_ENV}")
    return Path(value)


def test_fixed_runner_candidate_frozen_http_smoke(tmp_path: Path) -> None:
    """Fixed-runner entry: prove the candidate itself serves the frozen UI/API."""

    candidate_root = _required_candidate_root()
    reader = FrozenWorkbenchBrowseReader.from_candidate_root(candidate_root)
    routes = reader.route_keys()
    status_route = "/api/v1/kb/sources/candidate_review_only/wave1/status"
    theme_route = "/api/v1/kb/workbench/theme-groups?scope=master"
    detail_route = next(
        route
        for route in routes
        if re.fullmatch(r"/api/v1/kb/workbench/master-atomic/[^/?]+", route)
        and not route.endswith("/status")
    )
    crop_route = next(
        route
        for route in routes
        if re.fullmatch(
            r"/api/v1/kb/sources/candidate_review_only/wave1/nodes/"
            r"atomic_part/[^/?]+/question-crops/[^/?]+",
            route,
        )
    )
    assert status_route in routes
    assert theme_route in routes

    config = _release_config(tmp_path)
    with running_server(config) as server:
        _install_frozen_only(server, reader)
        headers = _origin(server)

        static_status, static_raw, _ = request(
            server, "GET", "/overlay/app.js", token=None
        )
        assert static_status == 200
        assert static_raw == reader.static("app.js").data

        for route in (status_route, theme_route, detail_route):
            status, body, _ = request(
                server, "GET", route, token=READ_TOKEN, headers=headers, timeout=20
            )
            assert status == 200, body
            assert _data(body) == reader.json(route)

        crop_status, crop_raw, _ = request(
            server,
            "GET",
            crop_route,
            token=READ_TOKEN,
            headers=headers,
            timeout=20,
        )
        assert crop_status == 200
        assert crop_raw == reader.crop(crop_route).data
