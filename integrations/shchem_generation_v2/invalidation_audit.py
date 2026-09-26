from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from .core import canonical_hash, sha256_file
from .schema_validation import load_schema, validate


WORKSPACE = Path(__file__).resolve().parents[2]
COORDINATION = WORKSPACE / "staging" / "coordination" / "generation_publication"
INITIAL_PATH = COORDINATION / "R13_FREEZE_INVALIDATED.json"
DRIFTED_PATH = COORDINATION / "history" / "R13_FREEZE_INVALIDATED_DRIFTED_SHA555FE2FC.json"
CORRECTION_PATH = COORDINATION / "R13_FREEZE_INVALIDATION_CORRECTION.json"
SCHEMA_PATH = (
    WORKSPACE
    / "integrations"
    / "shchem_generation_v2"
    / "schemas"
    / "invalidation_correction.schema.json"
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _live_ref(ref: dict[str, Any]) -> tuple[Path | None, list[str]]:
    errors: list[str] = []
    raw = ref.get("path")
    if (
        not isinstance(raw, str)
        or Path(raw).is_absolute()
        or re.match(r"^[A-Za-z]:[\\/]", raw)
        or raw.startswith(("\\\\", "//"))
        or ".." in Path(raw).parts
    ):
        return None, ["unsafe_or_missing_path"]
    path = (WORKSPACE / raw).resolve()
    try:
        path.relative_to(WORKSPACE.resolve())
    except ValueError:
        return None, ["path_outside_workspace"]
    if not path.is_file():
        return path, ["file_missing"]
    if sha256_file(path) != ref.get("sha256"):
        errors.append("sha256_mismatch")
    if path.stat().st_size != ref.get("bytes"):
        errors.append("bytes_mismatch")
    return path, errors


def validate_r13_invalidation_correction() -> dict[str, Any]:
    errors: list[str] = []
    try:
        record = _load(CORRECTION_PATH)
        validate(record, load_schema(SCHEMA_PATH))
    except Exception as exc:
        return {
            "check": "r13_append_only_invalidation_correction",
            "status": "fail",
            "errors": [f"schema_or_read_error:{exc}"],
        }
    unsigned = copy.deepcopy(record)
    claimed_self_hash = unsigned.pop("self_hash", None)
    if canonical_hash(unsigned) != claimed_self_hash:
        errors.append("canonical_self_hash_mismatch")
    recovery = record.get("change_event", {}).get("recovery_evidence", {})
    serialized_recovery = json.dumps(recovery, ensure_ascii=False)
    if (
        re.search(r"[A-Za-z]:[\\/]", serialized_recovery)
        or "\\\\" in serialized_recovery
        or str(recovery.get("evidence_locator", "")).startswith(("/", "\\"))
    ):
        errors.append("recovery_evidence_contains_host_or_absolute_path")
    if recovery.get("runtime_external_session_dependency") is not False:
        errors.append("runtime_external_session_dependency_must_be_false")
    path_mutation_cases: list[dict[str, Any]] = []
    for mutation_id, bad_locator in (
        ("windows_drive_absolute_path", r"C:\Users\example\session.jsonl"),
        ("unc_absolute_path", r"\\server\share\session.jsonl"),
        ("posix_absolute_path", "/var/tmp/session.jsonl"),
    ):
        mutation = copy.deepcopy(record)
        mutation["change_event"]["recovery_evidence"]["evidence_locator"] = bad_locator
        rejected = False
        try:
            validate(mutation, load_schema(SCHEMA_PATH))
        except Exception:
            rejected = True
        path_mutation_cases.append(
            {
                "mutation_id": mutation_id,
                "expected_rejected": True,
                "observed_rejected": rejected,
            }
        )
        if not rejected:
            errors.append(f"host_path_mutation_not_rejected:{mutation_id}")
    refs = {
        "initial_authoritative_record": record["initial_authoritative_record"],
        "drifted_snapshot": record["drifted_snapshot"],
        **record["bound_r13_artifacts"],
    }
    for label, ref in refs.items():
        _, ref_errors = _live_ref(ref)
        errors.extend(f"{label}:{error}" for error in ref_errors)
    if record["initial_authoritative_record"] != {
        "path": "staging/coordination/generation_publication/R13_FREEZE_INVALIDATED.json",
        "sha256": "96dab85b999d261ee9a87e96348d268336e16d2002345eda2f65d6a5aaad2b47",
        "bytes": 3395,
    }:
        errors.append("initial_authoritative_record_not_exact_audit_baseline")
    drift = record["drifted_snapshot"]
    if {key: drift.get(key) for key in ("sha256", "bytes")} != {
        "sha256": "555fe2fc0c1dd1af80daefb126d96404e82fee1a90d1215176a095aabe4865ec",
        "bytes": 3729,
    }:
        errors.append("drifted_snapshot_not_exact_observed_bytes")
    initial = _load(INITIAL_PATH) if INITIAL_PATH.is_file() else {}
    drifted = _load(DRIFTED_PATH) if DRIFTED_PATH.is_file() else {}
    for key in ("active_freeze", "review_dispatch", "external_review_a", "external_review_b"):
        if initial.get(key) != drifted.get(key):
            errors.append(f"historical_artifact_binding_changed:{key}")
        expected = record["bound_r13_artifacts"][
            {"active_freeze": "formal_freeze"}.get(key, key)
        ]
        comparable = {
            field: initial.get(key, {}).get(field)
            for field in expected
            if field != "authority_status"
        }
        if comparable != expected:
            errors.append(f"correction_artifact_binding_not_exact:{key}")
    return {
        "check": "r13_append_only_invalidation_correction",
        "status": "pass" if not errors else "fail",
        "r13_invalidated": not errors,
        "r13_active": False,
        "correction_sha256": sha256_file(CORRECTION_PATH),
        "initial_sha256": sha256_file(INITIAL_PATH) if INITIAL_PATH.is_file() else None,
        "drifted_snapshot_sha256": sha256_file(DRIFTED_PATH) if DRIFTED_PATH.is_file() else None,
        "host_path_privacy_mutation_cases": path_mutation_cases,
        "errors": errors,
    }
