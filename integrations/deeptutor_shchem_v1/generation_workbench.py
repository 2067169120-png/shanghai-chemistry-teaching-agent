"""Teacher-only public adapter for the append-only generation workbench.

The adapter intentionally exposes descriptors and gate results only.  Stored
paths, raw task-card bytes, source material, and full immutable records never
cross the gateway boundary.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from integrations.shchem_generation_workbench_v1 import (
    AppendOnlyWorkbenchStore,
    ContractError,
    IntegrityError,
    StateTransitionError,
    StoreConflictError,
    canonical_json_bytes,
    resolve_provider_request,
)

from .service import ApiError

_AUTHORITY_FALSE = {
    "human_reviewed": False,
    "teaching_use_allowed": False,
    "publication_allowed": False,
    "official": False,
    "official_claim_allowed": False,
    "external_publication_allowed": False,
    "release_allowed": False,
}
_FREEZE_KEYS = frozenset(
    {"expected_candidate_file_sha256", "expected_candidate_file_size_bytes"}
)
_PLAN_KEYS = frozenset(
    {
        "expected_candidate_file_sha256",
        "expected_candidate_file_size_bytes",
        "expected_freeze_file_sha256",
        "expected_freeze_file_size_bytes",
        "evidence_summaries",
    }
)
_REQUESTED_PROVIDER = resolve_provider_request(
    {"provider_profile_id": "sol_xhigh_generation_v1"}
)
_PROVIDER_STATUS = {
    "provider_profile_id": "sol_xhigh_generation_v1",
    "availability": "unknown",
    "status_record_id": "gateway-requested-config-only",
}


def _exact_keys(value: Any, expected: frozenset[str], label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ApiError("invalid_workbench_contract", f"{label} must be an object")
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing or extra:
        raise ApiError(
            "invalid_workbench_contract",
            f"{label} exact-key violation",
            400,
            {"missing": missing, "extra": extra},
        )
    return value


def _descriptor(data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "sha256": data["file_sha256"],
        "bytes": data["file_size_bytes"],
    }


def _requested_provider() -> dict[str, Any]:
    # A fresh JSON copy keeps callers from mutating the module-level constant.
    return json.loads(json.dumps(_REQUESTED_PROVIDER))


class GenerationWorkbenchGateway:
    """Narrow gateway surface over ``AppendOnlyWorkbenchStore``."""

    def __init__(self, gateway_state_root: Path):
        # Preserve the caller's raw leaf so the core can lstat it before any
        # resolution and reject symlink/reparse-point state roots.
        self.store = AppendOnlyWorkbenchStore(
            Path(gateway_state_root), "generation_workbench"
        )

    @staticmethod
    def _translate(exc: Exception, *, missing_code: str | None = None) -> ApiError:
        if isinstance(exc, ContractError):
            return ApiError("invalid_workbench_contract", str(exc), 400)
        if isinstance(exc, StoreConflictError):
            return ApiError(
                "immutable_state_conflict",
                "the append-only destination already exists",
                409,
            )
        if isinstance(exc, StateTransitionError):
            return ApiError("workbench_transition_rejected", str(exc), 409)
        if isinstance(exc, IntegrityError):
            text = str(exc).casefold()
            if "missing" in text and missing_code:
                return ApiError(missing_code, "workbench record not found", 404)
            if "stale" in text:
                return ApiError(
                    "stale_workbench_descriptor",
                    "the supplied immutable descriptor is stale",
                    409,
                )
            return ApiError(
                "workbench_integrity_failure",
                "append-only workbench integrity verification failed",
                409,
            )
        return ApiError("workbench_failure", "workbench operation failed", 500)

    @staticmethod
    def _assert_descriptor(
        observed: Mapping[str, Any], expected_sha256: Any, expected_bytes: Any, label: str
    ) -> None:
        if (
            type(expected_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
        ):
            raise ApiError(
                "invalid_workbench_contract", f"{label} SHA-256 is invalid", 400
            )
        if type(expected_bytes) is not int or expected_bytes <= 0:
            raise ApiError(
                "invalid_workbench_contract", f"{label} byte count is invalid", 400
            )
        if (
            observed["file_sha256"] != expected_sha256
            or observed["file_size_bytes"] != expected_bytes
        ):
            raise ApiError(
                "stale_workbench_descriptor",
                f"{label} SHA-256/bytes descriptor is stale",
                409,
            )

    @staticmethod
    def _task_projection(
        candidate: Mapping[str, Any], frozen: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "task_id": candidate["task_id"],
            "status": "frozen" if frozen else "candidate",
            "candidate": _descriptor(candidate),
            "requested_provider": _requested_provider(),
            "blockers": [],
            "task_card_content_returned": False,
            **_AUTHORITY_FALSE,
        }
        if frozen:
            result["freeze"] = _descriptor(frozen)
            result["candidate_snapshot"] = {
                "sha256": frozen["candidate_snapshot_sha256"],
                "bytes": frozen["candidate_snapshot_size_bytes"],
            }
        return result

    def create_task(self, task_card: dict[str, Any]) -> dict[str, Any]:
        try:
            raw = canonical_json_bytes(task_card)
            created = self.store.create_task_card_candidate(raw)
            return self._task_projection(created)
        except (ContractError, IntegrityError, StoreConflictError) as exc:
            raise self._translate(exc) from exc

    def get_task(self, task_id: str) -> dict[str, Any]:
        try:
            candidate = self.store.read_task_card_candidate(task_id)
        except (ContractError, IntegrityError) as exc:
            raise self._translate(exc, missing_code="workbench_task_not_found") from exc
        frozen: dict[str, Any] | None = None
        freeze_marker = self.store.state_root / "tasks" / task_id / "frozen" / "freeze_committed.json"
        if freeze_marker.is_file():
            try:
                frozen = self.store.read_frozen_task_card(task_id)
            except (ContractError, IntegrityError) as exc:
                raise self._translate(exc) from exc
        return self._task_projection(candidate, frozen)

    def list_tasks(self) -> dict[str, Any]:
        tasks_root = self.store.state_root / "tasks"
        entries = sorted(tasks_root.iterdir(), key=lambda item: item.name)
        items: list[dict[str, Any]] = []
        for entry in entries:
            if not entry.is_dir():
                raise ApiError(
                    "workbench_integrity_failure",
                    "unexpected append-only task-store entry",
                    409,
                )
            items.append(self.get_task(entry.name))
        return {
            "items": items,
            "count": len(items),
            "status": "ok",
            "read_is_idempotent": True,
            "requested_provider": _requested_provider(),
            **_AUTHORITY_FALSE,
        }

    def freeze_task(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        value = _exact_keys(body, _FREEZE_KEYS, "freeze_request")
        try:
            candidate = self.store.read_task_card_candidate(task_id)
            self._assert_descriptor(
                candidate,
                value["expected_candidate_file_sha256"],
                value["expected_candidate_file_size_bytes"],
                "candidate",
            )
            frozen = self.store.freeze_task_card(
                task_id, value["expected_candidate_file_sha256"]
            )
            return self._task_projection(candidate, frozen)
        except ApiError:
            raise
        except (ContractError, IntegrityError, StoreConflictError) as exc:
            raise self._translate(exc, missing_code="workbench_task_not_found") from exc

    def create_plan_run(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        value = _exact_keys(body, _PLAN_KEYS, "plan_run_request")
        try:
            committed = self.store.create_plan_run_transaction(
                task_id,
                expected_candidate_file_sha256=value[
                    "expected_candidate_file_sha256"
                ],
                expected_candidate_file_size_bytes=value[
                    "expected_candidate_file_size_bytes"
                ],
                expected_freeze_file_sha256=value["expected_freeze_file_sha256"],
                expected_freeze_file_size_bytes=value[
                    "expected_freeze_file_size_bytes"
                ],
                evidence_summaries=value["evidence_summaries"],
                provider_status=_PROVIDER_STATUS,
            )
            return self._run_projection(committed)
        except ApiError:
            raise
        except (
            ContractError,
            IntegrityError,
            StateTransitionError,
            StoreConflictError,
        ) as exc:
            raise self._translate(exc, missing_code="workbench_task_not_found") from exc

    @staticmethod
    def _event_projection(event: Mapping[str, Any]) -> dict[str, Any]:
        payload = event["record"]["payload"]
        item: dict[str, Any] = {
            "sequence": payload["event_sequence"],
            "event_type": payload["event_type"],
            "sha256": event["file_sha256"],
            "bytes": event["file_size_bytes"],
            "previous_event_sha256": payload["previous_event_file_sha256"],
            **_AUTHORITY_FALSE,
        }
        if payload["event_type"] == "evidence_preflight":
            report = payload["event_data"]
            blockers = list(report.get("blockers", []))
            item.update(
                {
                    "status": "plan_only_blocked" if blockers else "plan_only_complete",
                    "core_status": report.get("status"),
                    "core_effective_mode": report.get("effective_mode"),
                    "blockers": blockers,
                    "requested_provider": report.get(
                        "provider_configuration", _requested_provider()
                    ),
                }
            )
        return item

    def _read_package(self, run_id: str) -> dict[str, Any]:
        try:
            return self.store.read_plan_run(run_id)
        except (ContractError, IntegrityError) as exc:
            raise self._translate(exc, missing_code="workbench_run_not_found") from exc

    def _run_projection(self, package: Mapping[str, Any]) -> dict[str, Any]:
        events = [self._event_projection(event) for event in package["events"]]
        preflight = next(
            (item for item in reversed(events) if item["event_type"] == "evidence_preflight"),
            None,
        )
        if preflight is None:
            status = "plan_incomplete"
            blockers = [{"code": "evidence_preflight_missing", "message": "required=1, observed=0"}]
            provider = _requested_provider()
        else:
            status = preflight["status"]
            blockers = preflight["blockers"]
            provider = preflight["requested_provider"]
        return {
            "run_id": package["run_id"],
            "task_id": package["task_id"],
            "status": status,
            "event_count": len(events),
            "head_event": {"sha256": events[-1]["sha256"], "bytes": events[-1]["bytes"]},
            "blockers": blockers,
            "requested_provider": provider,
            "model_invoked": False,
            "contains_generated_questions": False,
            "actual_generation_performed": False,
            "download_available": False,
            **_AUTHORITY_FALSE,
        }

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._run_projection(self._read_package(run_id))

    def get_events(self, run_id: str) -> dict[str, Any]:
        package = self._read_package(run_id)
        events = [self._event_projection(event) for event in package["events"]]
        return {
            "run_id": run_id,
            "task_id": package["task_id"],
            "items": events,
            "count": len(events),
            "read_is_idempotent": True,
            **_AUTHORITY_FALSE,
        }

    def get_artifacts(self, run_id: str) -> dict[str, Any]:
        package = self._read_package(run_id)
        events = [self._event_projection(event) for event in package["events"]]
        artifact = package["artifact"]
        item = {
            "artifact_id": f"{run_id}.generation-plan",
            "artifact_type": "generation_plan",
            "sha256": artifact["file_sha256"],
            "bytes": artifact["file_size_bytes"],
            "status": next(
                (
                    event["status"]
                    for event in reversed(events)
                    if event["event_type"] == "evidence_preflight"
                ),
                "plan_incomplete",
            ),
            "contains_generated_questions": False,
            "download_available": False,
            **_AUTHORITY_FALSE,
        }
        return {
            "run_id": run_id,
            "task_id": package["task_id"],
            "items": [item],
            "count": 1,
            "download_available": False,
            "read_is_idempotent": True,
            **_AUTHORITY_FALSE,
        }
