from __future__ import annotations

"""Teacher-facing orchestration for candidate browse releases.

The gateway accepts no command, path, receipt, environment, or test recipe from
HTTP callers.  It materializes the fixed live browse source, delegates the
fixed seven-step regression to the trusted runner, and resolves receipts from
the owner-only ledger before selecting a release.  A selection only changes
the next-start pointer; the current process keeps its frozen serving bytes.
"""

import hashlib
import os
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import workbench_release_control as release_control
from .config import ServingReleaseBinding
from .workbench_release_control import (
    WorkbenchReleaseControlError,
    WorkbenchReleaseStore,
)
from .workbench_release_regression import (
    WorkbenchReleaseRegressionError,
    WorkbenchReleaseRegressionRunner,
    WorkbenchReleaseRegressionVerifier,
)
from .workbench_release_snapshot import (
    WorkbenchReleaseSnapshotError,
    WorkbenchReleaseSnapshotMaterializer,
)


WORKBENCH_RELEASE_PREPARE_CAPABILITY = "workbench_release_prepare_write"
WORKBENCH_RELEASE_ACTIVATE_CAPABILITY = "workbench_release_activate_write"
RELEASE_GATEWAY_SCHEMA_VERSION = "shchem.workbench.release_gateway.v1"
REGRESSION_RESERVATION_WAIT_SECONDS = 60.0
REGRESSION_RESERVATION_POLL_SECONDS = 0.02

_RELEASE_ID = re.compile(r"^WBREL-[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^WBRUN-[0-9a-f]{32}$")
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


class WorkbenchReleaseGatewayError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


def _error(code: str, message: str, status: int = 409) -> WorkbenchReleaseGatewayError:
    return WorkbenchReleaseGatewayError(code, message, status)


def _safe_failure(exc: BaseException) -> dict[str, Any]:
    return {
        "code": getattr(exc, "code", "workbench_release_operation_failed"),
        "message_zh": "候选浏览版本操作失败；未改变当前服务版本或教学权限。",
    }


class WorkbenchReleaseGateway:
    """Coordinate freeze, fixed regression, selection and historical rollback."""

    def __init__(
        self,
        workspace_root: Path,
        release_root: Path,
        serving: ServingReleaseBinding,
    ) -> None:
        self.workspace_root = workspace_root.resolve(strict=True)
        self.release_root = release_root.absolute()
        self.serving = serving
        self.verifier = WorkbenchReleaseRegressionVerifier(
            self.release_root, project_root=self.workspace_root
        )
        self.store = WorkbenchReleaseStore(
            self.release_root,
            project_root=self.workspace_root,
            pass_receipt_verifier=self.verifier,
            historical_replacement_receipt_verifier=(
                self.verifier.verify_historical_for_replacement_or_raise
            ),
        )
        self.runner = WorkbenchReleaseRegressionRunner(self.store)
        self.materializer = WorkbenchReleaseSnapshotMaterializer(self.workspace_root)
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="shchem-release-regression"
        )
        self._future: Future[dict[str, Any]] | None = None
        self._cancel_event: threading.Event | None = None
        self._run_release_id: str | None = None
        self._idempotency_key: str | None = None
        self._closed = False
        (
            self._selected_pointer_cache,
            self._selected_pointer_trust,
        ) = self._read_selected_pointer_for_control()
        self._selected_pointer_digest = self._active_pointer_digest()

    def _read_selected_pointer_for_control(
        self,
    ) -> tuple[dict[str, Any] | None, str]:
        """Read a current head, or a fully verified replacement-only old head."""

        try:
            pointer = self.store.read_active_pointer()
        except WorkbenchReleaseControlError:
            pointer = self.store.read_active_pointer_for_replacement()
            return pointer, "historical_for_replacement_only"
        return pointer, "current_trusted" if pointer is not None else "none"

    def _active_pointer_digest(self) -> str | None:
        path = self.store.active_pointer_path
        if not os.path.lexists(path):
            return None
        raw = release_control._read_file_stable(path, maximum_bytes=1024 * 1024)
        return hashlib.sha256(raw).hexdigest()

    def _selected_pointer(self) -> dict[str, Any] | None:
        """Reuse a fully verified pointer until its small canonical file changes."""

        digest = self._active_pointer_digest()
        with self._lock:
            if digest == self._selected_pointer_digest:
                return deepcopy(self._selected_pointer_cache)
        observed, observed_trust = self._read_selected_pointer_for_control()
        confirmed_digest = self._active_pointer_digest()
        if digest != confirmed_digest:
            raise _error(
                "workbench_release_pointer_drift",
                "selected release pointer changed during status verification",
            )
        with self._lock:
            self._selected_pointer_cache = deepcopy(observed)
            self._selected_pointer_trust = observed_trust
            self._selected_pointer_digest = confirmed_digest
            return deepcopy(observed)

    @staticmethod
    def _candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
        manifest = candidate["manifest"]
        closure = manifest["browse_closure"]
        return {
            "release_id": candidate["release_id"],
            "created_at": manifest["created_at"],
            "candidate_manifest_sha256": candidate["manifest_sha256"],
            "candidate_manifest_bytes": candidate["manifest_bytes"],
            "closure_sha256": closure["closure_sha256"],
            "artifact_count": closure["artifact_count"],
            "total_bytes": closure["total_bytes"],
            "candidate_only": True,
            "human_reviewed": False,
            "teaching_use_allowed": False,
            "publication_allowed": False,
        }

    @staticmethod
    def _pointer_summary(pointer: dict[str, Any] | None) -> dict[str, Any] | None:
        if pointer is None:
            return None
        return {
            "release_id": pointer["release_id"],
            "revision": pointer["revision"],
            "action": pointer["action"],
            "updated_at": pointer["updated_at"],
            "activation_sequence": pointer["activation_sequence"],
            "candidate_manifest_sha256": pointer["candidate_manifest_sha256"],
            "candidate_manifest_bytes": pointer["candidate_manifest_bytes"],
        }

    def _ensure_open(self) -> None:
        if self._closed:
            raise _error(
                "workbench_release_gateway_closed",
                "release gateway is shutting down",
                503,
            )

    def _memory_run_status(self) -> dict[str, Any] | None:
        future = self._future
        if future is None or self._run_release_id is None:
            return None
        if not future.done():
            value = {
                "release_id": self._run_release_id,
                "state": "running",
                "cancellable": True,
                "automatic_activation": False,
            }
            try:
                persisted = self.verifier.status_for_release(self._run_release_id)
            except Exception:  # noqa: BLE001 - reservation may be mid-commit
                persisted = {}
            if persisted.get("state") != "not_started":
                for key in ("run_id", "receipt_id", "reserved_at"):
                    if persisted.get(key) is not None:
                        value[key] = persisted[key]
            return value
        try:
            future.result()
        except Exception as exc:  # noqa: BLE001 - sanitized local task status
            return {
                "release_id": self._run_release_id,
                "state": "failed_closed",
                "failure": _safe_failure(exc),
                "cancellable": False,
                "automatic_activation": False,
            }
        try:
            return self.verifier.status_for_release(self._run_release_id)
        except Exception as exc:  # noqa: BLE001 - persisted status stays sanitized
            return {
                "release_id": self._run_release_id,
                "state": "failed_closed",
                "failure": _safe_failure(exc),
                "cancellable": False,
                "automatic_activation": False,
            }

    def status(self) -> dict[str, Any]:
        self._ensure_open()
        selected = self._selected_pointer()
        serving = self.serving.public_dict()
        selected_summary = self._pointer_summary(selected)
        restart_required = (
            selected_summary is not None
            and (
                serving["mode"] != "frozen"
                or selected_summary["release_id"] != serving["release_id"]
                or selected_summary["revision"] != serving["selected_revision"]
            )
        )
        with self._lock:
            active_run = self._memory_run_status()
            selected_trust_state = self._selected_pointer_trust
        selected_trust = {
            "state": selected_trust_state,
            "current_trusted": selected_trust_state == "current_trusted",
            "replacement_trusted": selected_trust_state
            in {"current_trusted", "historical_for_replacement_only"},
            "serving_eligible": selected_trust_state == "current_trusted",
            "rollback_eligible": selected_trust_state == "current_trusted",
            "replacement_only": selected_trust_state
            == "historical_for_replacement_only",
        }
        return {
            "schema_version": RELEASE_GATEWAY_SCHEMA_VERSION,
            "serving_release": serving,
            "selected_release": selected_summary,
            "selected_release_trust": selected_trust,
            "restart_required": restart_required,
            "selection_effect_policy": "next_clean_restart_only",
            "active_regression": active_run,
            "automatic_activation": False,
            "client_supplied_commands_allowed": False,
            "candidate_only": True,
            "human_reviewed": False,
            "teaching_use_allowed": False,
            "publication_allowed": False,
        }

    def list_candidates(self) -> dict[str, Any]:
        self._ensure_open()
        items: list[dict[str, Any]] = []
        for candidate in self.store.list_candidates():
            item = self._candidate_summary(candidate)
            item["regression"] = self.verifier.status_for_release(
                candidate["release_id"]
            )
            items.append(item)
        return {
            "schema_version": RELEASE_GATEWAY_SCHEMA_VERSION,
            "items": items,
            "count": len(items),
            "candidate_only": True,
            "automatic_activation": False,
        }

    def freeze_candidate(self) -> dict[str, Any]:
        self._ensure_open()
        with self._lock:
            if self._future is not None and not self._future.done():
                raise _error(
                    "workbench_release_regression_active",
                    "a fixed regression is active; freeze is temporarily unavailable",
                )
            closure = self.materializer.materialize()
            candidate = self.store.freeze_candidate(closure)
        return {
            "candidate": self._candidate_summary(candidate),
            "regression": self.verifier.status_for_release(candidate["release_id"]),
            "selection_committed": False,
            "serving_changed": False,
            "automatic_activation": False,
        }

    def start_regression(
        self, release_id: str, *, idempotency_key: str
    ) -> dict[str, Any]:
        self._ensure_open()
        if not isinstance(release_id, str) or not _RELEASE_ID.fullmatch(release_id):
            raise _error("workbench_release_id_invalid", "release id is invalid", 400)
        if (
            not isinstance(idempotency_key, str)
            or not _IDEMPOTENCY.fullmatch(idempotency_key)
        ):
            raise _error(
                "workbench_release_idempotency_invalid",
                "regression idempotency key is invalid",
                400,
            )
        self.store.read_candidate(release_id)
        persisted = self.verifier.status_for_release(release_id)
        if persisted["state"] != "not_started":
            return {"run": persisted, "idempotent": True, "_http_status": 200}
        with self._lock:
            if self._future is not None and not self._future.done():
                if (
                    self._run_release_id == release_id
                    and self._idempotency_key == idempotency_key
                ):
                    return {
                        "run": self._memory_run_status(),
                        "idempotent": True,
                        "_http_status": 202,
                    }
                raise _error(
                    "workbench_release_regression_active",
                    "another fixed regression is already active",
                )
            cancellation = threading.Event()
            self._cancel_event = cancellation
            self._run_release_id = release_id
            self._idempotency_key = idempotency_key
            self._future = self._executor.submit(
                self.runner.run, release_id, cancel_event=cancellation
            )
        run = self._wait_for_committed_regression_reservation()
        return {"run": run, "idempotent": False, "_http_status": 202}

    def _wait_for_committed_regression_reservation(self) -> dict[str, Any]:
        """Wait for the runner ledger to expose a real committed run identity."""

        deadline = time.monotonic() + REGRESSION_RESERVATION_WAIT_SECONDS
        while True:
            run = self._memory_run_status()
            if run is not None and run.get("run_id") is not None:
                return run
            future = self._future
            if future is not None and future.done():
                # One final observation covers a reservation committed just
                # before the worker completed or failed.
                run = self._memory_run_status()
                if run is not None and run.get("run_id") is not None:
                    return run
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(REGRESSION_RESERVATION_POLL_SECONDS, remaining))

        # No uncommitted or fabricated identity is returned.  A worker that is
        # still computing its fingerprint is asked to cancel; shutdown remains
        # responsible for joining the single worker thread.
        with self._lock:
            if (
                self._future is not None
                and not self._future.done()
                and self._cancel_event is not None
            ):
                self._cancel_event.set()
        raise _error(
            "workbench_release_regression_reservation_unavailable",
            "fixed regression reservation was not committed in time",
            503,
        )

    def regression_status(self, release_id: str, run_id: str) -> dict[str, Any]:
        self._ensure_open()
        if not isinstance(release_id, str) or not _RELEASE_ID.fullmatch(release_id):
            raise _error("workbench_release_id_invalid", "release id is invalid", 400)
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise _error("workbench_release_run_id_invalid", "run id is invalid", 400)
        with self._lock:
            memory = self._memory_run_status()
            if memory is not None and memory.get("release_id") == release_id:
                observed_run_id = memory.get("run_id")
                if observed_run_id is None or observed_run_id == run_id:
                    return {"run": memory}
        persisted = self.verifier.status_for_release(release_id)
        if persisted.get("run_id") != run_id:
            raise _error(
                "workbench_release_run_not_found",
                "regression run does not belong to this release",
                404,
            )
        return {"run": persisted}

    def cancel_regression(self, release_id: str, run_id: str) -> dict[str, Any]:
        self._ensure_open()
        if not _RELEASE_ID.fullmatch(str(release_id)) or not _RUN_ID.fullmatch(
            str(run_id)
        ):
            raise _error(
                "workbench_release_run_id_invalid", "regression run identity is invalid", 400
            )
        with self._lock:
            memory = self._memory_run_status()
            if (
                memory is None
                or memory.get("release_id") != release_id
                or memory.get("run_id") not in {None, run_id}
                or self._future is None
                or self._future.done()
                or self._cancel_event is None
            ):
                raise _error(
                    "workbench_release_run_not_cancellable",
                    "regression run is not active in this process",
                )
            self._cancel_event.set()
            return {
                "run": {**memory, "cancellation_requested": True},
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
        self._ensure_open()
        receipt = self.verifier.retrieve_receipt(run_id)
        if receipt.get("release_id") != release_id:
            raise _error(
                "workbench_release_run_binding_mismatch",
                "trusted regression run belongs to another release",
            )
        if action == "activate":
            selected = self.store.activate_release(
                release_id,
                receipt,
                expected_revision=expected_revision,
                principal_id=principal_id,
                reason_zh=reason_zh,
            )
        else:
            if expected_revision is None:
                raise _error(
                    "workbench_release_revision_required",
                    "rollback requires the selected revision",
                    400,
                )
            selected = self.store.rollback_to_release(
                release_id,
                receipt,
                expected_revision=expected_revision,
                principal_id=principal_id,
                reason_zh=reason_zh,
            )
        pointer = self._pointer_summary(selected["active_pointer"])
        with self._lock:
            self._selected_pointer_cache = deepcopy(selected["active_pointer"])
            self._selected_pointer_trust = "current_trusted"
            self._selected_pointer_digest = self._active_pointer_digest()
        return {
            "selected_release": pointer,
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
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if (
                self._future is not None
                and not self._future.done()
                and self._cancel_event is not None
            ):
                self._cancel_event.set()
        self._executor.shutdown(wait=True, cancel_futures=False)


RELEASE_GATEWAY_ERRORS = (
    WorkbenchReleaseGatewayError,
    WorkbenchReleaseControlError,
    WorkbenchReleaseRegressionError,
    WorkbenchReleaseSnapshotError,
)


__all__ = [
    "RELEASE_GATEWAY_ERRORS",
    "RELEASE_GATEWAY_SCHEMA_VERSION",
    "WORKBENCH_RELEASE_ACTIVATE_CAPABILITY",
    "WORKBENCH_RELEASE_PREPARE_CAPABILITY",
    "WorkbenchReleaseGateway",
    "WorkbenchReleaseGatewayError",
]
