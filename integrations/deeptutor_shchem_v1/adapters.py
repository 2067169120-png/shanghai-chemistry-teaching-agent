from __future__ import annotations

import csv
import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from .config import CONTRACT_VERSION
from .security import safe_join, validate_identifier

CONTROLLER_CONTRACT = "2.0.0"


class AdapterUnavailable(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _sanitized_environment() -> dict[str, str]:
    """Pass only process/runtime basics; OAuth and provider secrets are excluded."""

    allowed = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


class ControllerRpcAdapter:
    """Fail-closed adapter for controller v2's stable JSON stdout CLI."""

    def __init__(
        self,
        shchem_root: Path,
        script: Path | None,
        timeout_seconds: float,
        allowed_input_roots: tuple[Path, ...] = (),
    ):
        self.shchem_root = shchem_root.resolve()
        self.expected_script = safe_join(
            self.shchem_root, "scripts", "sh_chem_agent.py"
        )
        self.script = script.resolve() if script else self.expected_script
        self.timeout_seconds = timeout_seconds
        self.allowed_input_roots = (
            self.shchem_root,
            *(path.resolve() for path in allowed_input_roots),
        )

    def availability(self) -> dict[str, Any]:
        path_allowed = self.script == self.expected_script
        return {
            "available": path_allowed and self.script.is_file(),
            "contract_version": CONTROLLER_CONTRACT,
            "expected_relative_path": "scripts/sh_chem_agent.py",
            "path_allowed": path_allowed,
            "reason": None
            if path_allowed and self.script.is_file()
            else "controller_missing_or_path_not_allowed",
            "live_status": "unknown_not_executed",
            "live_validate": "unknown_not_executed",
        }

    def call(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        availability = self.availability()
        if not availability["available"]:
            raise AdapterUnavailable(
                "controller_unavailable",
                "Shanghai Chemistry controller is unavailable; operation failed closed",
                availability,
            )
        command = self._command(operation, payload)
        try:
            completed = subprocess.run(
                command,
                cwd=self.shchem_root,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=self.timeout_seconds,
                env=_sanitized_environment(),
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterUnavailable(
                "controller_timeout",
                "controller call timed out",
                {"operation": operation},
            ) from exc
        except OSError as exc:
            raise AdapterUnavailable(
                "controller_start_failed", "controller could not be started"
            ) from exc
        try:
            response = json.loads(completed.stdout or "")
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise AdapterUnavailable(
                "controller_response_invalid",
                "controller did not return stable JSON",
                {"operation": operation, "exit_code": completed.returncode},
            ) from exc
        expected_command = (
            "preflight" if operation == "publication.preflight" else operation
        )
        if response.get("contract_version") != CONTROLLER_CONTRACT:
            raise AdapterUnavailable(
                "controller_contract_mismatch", "controller response version mismatch"
            )
        if response.get("command") != expected_command:
            raise AdapterUnavailable(
                "controller_command_mismatch", "controller response command mismatch"
            )
        response = dict(response)
        response["controller_exit_code"] = completed.returncode
        response["live_controller_output"] = True
        if operation == "status" and completed.returncode != 0:
            raise AdapterUnavailable(
                "controller_nonzero",
                "controller status failed",
                {"exit_code": completed.returncode, "response": response},
            )
        return response

    def _command(self, operation: str, payload: dict[str, Any]) -> list[str]:
        base = [sys.executable, str(self.script)]
        root_arg = ["--root", str(self.shchem_root)]
        if operation in {"status", "validate"}:
            return [*base, operation, *root_arg]
        if operation == "query":
            query_text = str(payload.get("text", ""))
            limit = min(max(int(payload.get("limit", 20)), 1), 100)
            command = [
                *base,
                "query",
                *root_arg,
                "--text",
                query_text,
                "--limit",
                str(limit),
            ]
            purpose = payload.get("purpose")
            if purpose:
                command.extend(["--purpose", str(purpose)])
            if payload.get("eligible_only") is True:
                command.append("--eligible-only")
            return command
        if operation in {"preflight", "publication.preflight"}:
            mode = (
                "publish"
                if operation == "publication.preflight"
                else str(payload.get("mode", ""))
            )
            allowed_modes = {
                "ingest",
                "diagnose_synthetic",
                "diagnose",
                "generate",
                "figure",
                "publish_smoke",
                "publish",
            }
            if mode not in allowed_modes:
                raise AdapterUnavailable(
                    "invalid_preflight_mode", "unsupported controller preflight mode"
                )
            command = [*base, "preflight", *root_arg, "--mode", mode]
            for payload_key, flag in (
                ("input_path", "--input"),
                ("formal_registry_path", "--formal-registry"),
                ("governance_chain_path", "--governance-chain"),
                ("profile_path", "--profile"),
            ):
                value = payload.get(payload_key)
                if value:
                    command.extend([flag, str(self._allowed_input_path(value))])
            return command
        if operation == "diagnosis-receipt-verify":
            input_path = self._allowed_input_path(payload.get("input_path"))
            receipt_path = self._allowed_input_path(payload.get("receipt_path"))
            return [
                *base,
                "diagnosis-receipt-verify",
                *root_arg,
                "--input",
                str(input_path),
                "--receipt",
                str(receipt_path),
            ]
        raise AdapterUnavailable(
            "controller_operation_not_supported",
            f"controller v2 does not expose operation: {operation}",
        )

    def _allowed_input_path(self, value: Any) -> Path:
        candidate = Path(str(value)).resolve()
        if not candidate.is_file() or not any(
            candidate == root or root in candidate.parents
            for root in self.allowed_input_roots
        ):
            raise AdapterUnavailable(
                "controller_input_path_rejected",
                "controller input is missing or outside the configured roots",
            )
        return candidate


class FixtureAdapter:
    """Explicit synthetic adapter used only when the gateway mode is `mock`."""

    def __init__(self, fixture_path: Path):
        self.path = fixture_path.resolve()
        self.fixture = json.loads(self.path.read_text(encoding="utf-8"))
        if self.fixture.get("contract_version") != CONTRACT_VERSION:
            raise ValueError("fixture contract version mismatch")
        if self.fixture.get("fixture_scope") != "synthetic_fixture_only":
            raise ValueError("mock fixture must declare synthetic_fixture_only")

    def availability(self) -> dict[str, Any]:
        return {
            "available": True,
            "mode": "explicit_mock",
            "fixture_scope": "synthetic_fixture_only",
            "fixture_sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
            "live_status": "not_applicable_mock",
            "live_validate": "not_applicable_mock",
        }

    def call(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if operation == "status":
            return dict(self.fixture.get("status", {}))
        if operation == "evidence.search":
            query = str(payload.get("query", "")).casefold().strip()
            limit = min(max(int(payload.get("limit", 20)), 1), 100)
            matches = []
            for item in self.fixture.get("evidence", []):
                haystack = " ".join(str(value) for value in item.values()).casefold()
                if not query or query in haystack:
                    matches.append(dict(item))
            return {
                "items": matches[:limit],
                "count": len(matches[:limit]),
                "scope": "synthetic_fixture_only",
            }
        if operation == "evidence.get":
            evidence_id = str(payload.get("evidence_id", ""))
            for item in self.fixture.get("evidence", []):
                if item.get("evidence_id") == evidence_id:
                    return {"item": dict(item), "scope": "synthetic_fixture_only"}
            raise AdapterUnavailable(
                "evidence_not_found", "synthetic evidence not found"
            )
        if operation == "figure.get":
            figure_id = str(payload.get("figure_id", ""))
            for item in self.fixture.get("figures", []):
                if item.get("figure_id") == figure_id:
                    return dict(item)
            raise AdapterUnavailable("figure_not_found", "synthetic figure not found")
        if operation == "preflight":
            return self._preflight(payload)
        if operation == "student.diagnosis":
            gate = self._preflight(payload)
            if not gate["ready"]:
                return {"status": "blocked", "preflight": gate}
            result = dict(self.fixture.get("diagnosis_candidate", {}))
            result.update(
                {
                    "status": "candidate",
                    "claim_scope": "synthetic_fixture_only",
                    "review_kind": "machine_only",
                    "human_reviewed": False,
                    "teaching_use_allowed": False,
                    "publication_allowed": False,
                }
            )
            return result
        if operation == "student.learning_view":
            result = dict(self.fixture.get("learning_history", {}))
            result.update(
                {
                    "profile_id": payload.get("student_id"),
                    "claim_scope": "synthetic_fixture_only",
                    "long_term_teaching_effectiveness_verified": False,
                    "machine_only": True,
                    "human_reviewed": False,
                }
            )
            return result
        if operation == "handout.candidate":
            gate = self._preflight(payload)
            if not gate["ready"]:
                return {"status": "blocked", "preflight": gate}
            result = dict(self.fixture.get("handout_candidate", {}))
            result.update(
                {
                    "status": "candidate",
                    "claim_scope": "synthetic_fixture_only",
                    "review_kind": "machine_only",
                    "human_reviewed": False,
                    "teaching_use_allowed": False,
                    "publication_allowed": False,
                }
            )
            return result
        if operation == "generation.candidate":
            gate = self._preflight(payload)
            if not gate["ready"]:
                return {"status": "blocked", "preflight": gate}
            result = dict(self.fixture.get("generation_candidate", {}))
            content = dict(result.get("diagnostic_content", {}))
            hierarchy = dict(content.get("subject_hierarchy", {}))
            atomic_ids = list(hierarchy.get("atomic_part_ids", []))
            printed_ids = list(hierarchy.get("printed_question_ids", []))
            parent_map = dict(hierarchy.get("atomic_to_printed", {}))
            hierarchy.update(
                {
                    "printed_count": len(printed_ids),
                    "atomic_count": len(atomic_ids),
                    "exact_registered_child_count": len(
                        hierarchy.get("exact_registered_child_ids", [])
                    ),
                    "counts_derived_not_constant": True,
                }
            )
            content["subject_hierarchy"] = hierarchy
            content["catalog_item_count"] = len(atomic_ids)
            content["content_only_child_chain"] = [
                {
                    "atomic_part_id": atomic_id,
                    "printed_question_id": parent_map.get(atomic_id),
                    "theme_id": "SYNTHETIC-THEME",
                    "governance": {
                        "state": "synthetic_fixture_only",
                        "human_reviewed": False,
                    },
                }
                for atomic_id in atomic_ids
            ]
            result["diagnostic_content"] = content
            result.update(
                {
                    "status": "candidate",
                    "claim_scope": "synthetic_fixture_only",
                    "machine_only": True,
                    "human_reviewed": False,
                    "teaching_use_allowed": False,
                    "publication_allowed": False,
                }
            )
            return result
        if operation == "publication.preflight":
            return {
                "ready": False,
                "status": "blocked",
                "machine_only": True,
                "publication_allowed": False,
                "blockers": [
                    "synthetic_fixture_not_publishable",
                    "human_review_cancelled_or_absent",
                    "external_release_verifier_absent",
                ],
            }
        raise AdapterUnavailable(
            "unsupported_operation", f"unsupported operation: {operation}"
        )

    @staticmethod
    def _preflight(payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("synthetic_fixture") is not True:
            return {
                "ready": False,
                "status": "blocked",
                "claim_scope": "real_or_unspecified",
                "machine_only": True,
                "blockers": [
                    "mock_requires_explicit_synthetic_fixture_true",
                    "real_student_diagnosis_gate_not_available_in_mock",
                ],
            }
        return {
            "ready": True,
            "status": "machine_pass",
            "claim_scope": "synthetic_fixture_only",
            "machine_only": True,
            "human_reviewed": False,
            "teaching_use_allowed": False,
            "publication_allowed": False,
            "blockers": [],
        }


class CatalogEvidenceReader:
    """Read-only candidate search over catalog.csv; never claims retrieval readiness."""

    PUBLIC_FIELDS = (
        "record_id",
        "year",
        "region_or_school",
        "paper_type",
        "content_type",
        "title",
        "source_account",
        "source_url",
        "published_at",
        "completeness",
        "answer_status",
        "rubric_status",
        "sha256",
        "verification_status",
        "notes",
    )

    def __init__(self, shchem_root: Path):
        self.root = shchem_root.resolve()
        self.catalog_path = safe_join(self.root, "catalog.csv")

    def _rows(self) -> list[dict[str, str]]:
        if not self.catalog_path.is_file():
            raise AdapterUnavailable(
                "catalog_missing", "read-only catalog is unavailable"
            )
        with self.catalog_path.open("r", encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))

    def search(self, query: str, limit: int) -> dict[str, Any]:
        normalized = query.casefold().strip()
        items = []
        for row in self._rows():
            public = {field: row.get(field, "") for field in self.PUBLIC_FIELDS}
            haystack = " ".join(public.values()).casefold()
            if not normalized or normalized in haystack:
                public["evidence_id"] = public.pop("record_id")
                public["retrieval_eligible"] = False
                public["claim_scope"] = (
                    "catalog_candidate_only_central_validation_unknown"
                )
                items.append(public)
                if len(items) >= limit:
                    break
        return {
            "items": items,
            "count": len(items),
            "claim_scope": "catalog_candidate_only_central_validation_unknown",
            "central_status": "unknown_controller_missing_or_not_called",
        }

    def get(self, evidence_id: str) -> dict[str, Any]:
        validate_identifier(evidence_id, "evidence_id")
        for row in self._rows():
            if row.get("record_id") == evidence_id:
                public = {field: row.get(field, "") for field in self.PUBLIC_FIELDS}
                public["evidence_id"] = public.pop("record_id")
                public["retrieval_eligible"] = False
                public["content_exposed"] = False
                public["claim_scope"] = (
                    "catalog_candidate_only_central_validation_unknown"
                )
                return {"item": public}
        raise AdapterUnavailable("evidence_not_found", "evidence record not found")


class CCSwitchClient:
    """Optional local-only DeepTutor/Sol egress path with no OAuth token access."""

    BASE_URL = "http://127.0.0.1:15721"

    def __init__(self, enabled: bool, base_url: str, timeout_seconds: float):
        if base_url != self.BASE_URL:
            raise ValueError("CCSwitch base URL must be loopback port 15721")
        self.enabled = enabled
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds

    def status(self) -> dict[str, Any]:
        if not self.enabled:
            return {
                "enabled": False,
                "available": False,
                "reason": "proxy_disabled",
                "oauth_token_accessed": False,
            }
        try:
            with socket.create_connection(
                ("127.0.0.1", 15721), timeout=self.timeout_seconds
            ):
                available = True
        except OSError:
            available = False
        return {
            "enabled": True,
            "available": available,
            "base_url": self.base_url,
            "reason": None if available else "loopback_proxy_unreachable",
            "oauth_token_accessed": False,
        }

    def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise AdapterUnavailable(
                "proxy_disabled", "CCSwitch proxy is disabled; no model call was made"
            )
        endpoint = self.base_url + "/v1/chat/completions"
        request = urlrequest.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlrequest.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read(2 * 1024 * 1024)
        except (OSError, urlerror.URLError, urlerror.HTTPError) as exc:
            raise AdapterUnavailable(
                "proxy_call_failed",
                "CCSwitch loopback call failed; success was not assumed",
            ) from exc
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdapterUnavailable(
                "proxy_response_invalid", "CCSwitch returned invalid JSON"
            ) from exc
        return {
            "provider": "ccswitch_loopback",
            "response": parsed,
            "oauth_token_accessed": False,
        }
