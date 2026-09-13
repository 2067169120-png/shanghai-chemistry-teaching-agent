from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

from .public_kb import ReadOnlyDataError, _checked_exact_path, _json_bytes


SLOTS = (
    "prefreeze_deterministic_qa",
    "review_a",
    "review_b",
    "adversarial",
    "chain",
    "registration",
)
REVIEW_SLOT_NAMES = {"review_a": "sol_review_a", "review_b": "sol_review_b"}
SAFE_ATOMIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SAFE_AXIS = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_FINDINGS = 24
MAX_FINDING_CHARS = 240
MAX_REASON_CODES = 16


def _path_components_exact(root: Path, relative: str) -> bool:
    """Require the caller-supplied spelling of every on-disk path component."""

    if not relative or "\\" in relative or Path(relative).is_absolute():
        return False
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    current = root.absolute()
    for part in parts:
        try:
            matches = [entry for entry in current.iterdir() if entry.name == part]
        except OSError:
            return False
        if len(matches) != 1:
            return False
        current = matches[0]
    return True


def _checked_governance_path(root: Path, relative: str) -> Path:
    path = _checked_exact_path(root, relative)
    if not _path_components_exact(root, relative):
        raise ReadOnlyDataError(
            "r18_path_case_mismatch",
            "R18 governance path spelling does not match the trusted artifact",
            403,
        )
    return path


def _descriptor(data: bytes) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _canonical_hash_without(document: dict[str, Any], field: str) -> str:
    payload = dict(document)
    payload.pop(field, None)
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _safe_summary(value: object) -> str:
    if not isinstance(value, str):
        return ""
    # Findings are public summaries, never verbatim evidence.  Collapse control
    # characters and hard-bound the result so the projection cannot become an
    # evidence or prompt exfiltration channel.
    compact = " ".join(value.split())
    compact = "".join(character for character in compact if character.isprintable())
    unsafe = compact.casefold()
    if (
        re.search(r"(?<![a-z0-9])[a-z]:[\\/]", compact, re.IGNORECASE)
        or "\\\\" in compact
        or "file://" in unsafe
        or re.search(
            r"(?<![a-z0-9_])(prompt|evidence|thread|turn|host)(?![a-z0-9_])",
            unsafe,
        )
        or any(
            marker in unsafe
            for marker in (
                "staging/",
                "sh-chem-db/",
                "integrations/",
                "review_prompt",
                "system prompt",
                "thread_id",
                "turn_id",
                "host_id",
            )
        )
    ):
        return "[unsafe detail redacted]"
    return compact[:MAX_FINDING_CHARS]


def _reason_codes(errors: object) -> list[str]:
    values = [str(item).casefold() for item in errors] if isinstance(errors, list) else []
    result: list[str] = []

    def add(code: str) -> None:
        if code not in result and len(result) < MAX_REASON_CODES:
            result.append(code)

    joined = "\n".join(values)
    sha_mismatch_count = sum("sha256_mismatch" in item for item in values)
    if (
        "live_fingerprint_mismatch" in joined
        or "producer_receipt" in joined and "mismatch" in joined
        or sha_mismatch_count >= 2
    ):
        add("partial_candidate_rebuild")
    if "ambigu" in joined or "duplicate" in joined or "collision" in joined:
        add("active_selector_ambiguous")
    if "invalidated" in joined or "invalidation_marker" in joined:
        add("active_state_invalidated")
    if "corpus" in joined or "correction" in joined:
        add("invalidation_correction_invalid")
    if "stale" in joined:
        add("stale_evidence")
    if "subject" in joined and "mismatch" in joined:
        add("subject_mismatch")
    if "slot" in joined and ("mismatch" in joined or "invalid" in joined):
        add("slot_mismatch")
    if "bytes_mismatch" in joined:
        add("bytes_mismatch")
    if (
        "sha256_mismatch" in joined
        or "hash_mismatch" in joined
        or "hash_bytes_mismatch" in joined
    ):
        add("hash_mismatch")
    if "observation" in joined:
        if "observation_missing" in joined:
            add("observation_missing")
        add("observation_invalid")
    if "attestation" in joined:
        if "attestation_missing" in joined:
            add("task_attestation_missing")
        add("task_attestation_invalid")
    if "receipt_missing" in joined:
        add("receipt_missing")
    if "legacy_or_non_activation_scoped_receipt" in joined:
        add("legacy_root_receipt_rejected")
    if "activation_scoped_receipt_missing" in joined:
        add("activation_scoped_receipt_missing")
    if "activation" in joined and "missing" in joined:
        add("activation_missing")
    if "fallback" in joined:
        add("no_legacy_fallback")
    if "malformed" in joined or "schema" in joined or "invalid" in joined:
        add("integrity_blocked")
    if values and not result:
        add("integrity_blocked")
    return result


def _slot(
    name: str,
    *,
    state: str,
    verdict: str,
    reasons: list[str] | tuple[str, ...] = (),
    failed_ids: list[str] | None = None,
    axes: list[str] | None = None,
    findings: list[dict[str, str]] | None = None,
    artifact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "slot": name,
        "verdict": verdict,
        "state": state,
        "failed_atomic_part_ids": failed_ids or [],
        "check_axes": axes or [],
        "finding_summaries": findings or [],
        "reason_codes": list(dict.fromkeys(reasons))[:MAX_REASON_CODES],
        "artifact": artifact,
    }


class R18GovernanceView:
    """Fail-closed, metadata-only projection of controller-selected R18 state."""

    REGISTRY_PATH = "sh-chem-db/kb/machine_governance_v2/chain_registry.json"

    def __init__(
        self,
        shchem_root: Path,
        *,
        selector: Callable[..., dict[str, object]] | None = None,
    ):
        self.root = shchem_root.absolute()
        self.workspace = self.root.parent
        self._selector = selector

    def _controller_selector(self) -> Callable[..., dict[str, object]]:
        if self._selector is not None:
            return self._selector
        scripts = str((self.root / "scripts").absolute())
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        # The endpoint is zero-write.  In particular, importing a controller in
        # a fresh fixture must not create __pycache__ beside trusted evidence.
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            from controller_v2.formal_freeze import select_active_r18_state
        except (ImportError, OSError) as exc:
            raise ReadOnlyDataError(
                "r18_controller_unavailable", "R18 controller selector is unavailable"
            ) from exc
        finally:
            sys.dont_write_bytecode = previous
        return select_active_r18_state

    def _read_ref(
        self, reference: object, *, json_object: bool = True
    ) -> tuple[dict[str, Any] | None, bytes, dict[str, Any], list[str]]:
        errors: list[str] = []
        if not isinstance(reference, dict) or set(reference) != {
            "path",
            "sha256",
            "bytes",
        }:
            return None, b"", {}, ["reference_shape_invalid"]
        relative = reference.get("path")
        digest = reference.get("sha256")
        size = reference.get("bytes")
        if (
            not isinstance(relative, str)
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or not isinstance(size, int)
            or size < 0
        ):
            return None, b"", {}, ["reference_shape_invalid"]
        try:
            path = _checked_governance_path(self.workspace, relative)
            data = path.read_bytes()
        except (OSError, ReadOnlyDataError):
            return None, b"", {}, ["referenced_artifact_missing"]
        actual = _descriptor(data)
        if actual["sha256"] != digest:
            errors.append("sha256_mismatch")
        if actual["bytes"] != size:
            errors.append("bytes_mismatch")
        document: dict[str, Any] | None = None
        if json_object:
            try:
                document = _json_bytes(data, "R18 governance artifact")
            except ReadOnlyDataError:
                errors.append("json_invalid")
        return document, data, actual, errors

    def _selector_result(self) -> dict[str, object]:
        try:
            value = self._controller_selector()(self.root, state="phase1_activation")
        except (OSError, RuntimeError, ValueError, TypeError):
            return {
                "valid": False,
                "selected": None,
                "legacy_fallback_used": False,
                "errors": ["active_selector_unavailable"],
            }
        if not isinstance(value, dict):
            return {
                "valid": False,
                "selected": None,
                "legacy_fallback_used": False,
                "errors": ["active_selector_invalid"],
            }
        return value

    @staticmethod
    def _ref_identity(reference: object) -> tuple[str, int] | None:
        if not isinstance(reference, dict):
            return None
        digest, size = reference.get("sha256"), reference.get("bytes")
        if isinstance(digest, str) and isinstance(size, int):
            return digest, size
        return None

    @classmethod
    def _subjects_match(cls, left: object, right: object) -> bool:
        if not isinstance(left, dict) or not isinstance(right, dict):
            return False
        expected_keys = {"question", "answer", "subject_pair_sha256"}
        if set(left) != expected_keys or set(right) != expected_keys:
            return False
        for key in ("question", "answer"):
            # A content-equivalent artifact at a different path is not the
            # activation-bound subject.  Bind the complete path/hash/bytes ref.
            left_ref, right_ref = left.get(key), right.get(key)
            if (
                not isinstance(left_ref, dict)
                or not isinstance(right_ref, dict)
                or set(left_ref) != {"path", "sha256", "bytes"}
                or set(right_ref) != {"path", "sha256", "bytes"}
                or left_ref != right_ref
            ):
                return False
        return (
            isinstance(left.get("subject_pair_sha256"), str)
            and left.get("subject_pair_sha256") == right.get("subject_pair_sha256")
        )

    def _prefreeze(
        self, activation: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        inputs = activation.get("immutable_inputs")
        if not isinstance(inputs, dict):
            return _slot(
                SLOTS[0], state="blocked", verdict="blocked", reasons=["immutable_inputs_missing"]
            ), {}
        _, _, _, question_errors = self._read_ref(
            inputs.get("question_subject"), json_object=False
        )
        _, _, _, answer_errors = self._read_ref(
            inputs.get("answer_subject"), json_object=False
        )
        request, request_bytes, request_artifact, request_errors = self._read_ref(
            inputs.get("deterministic_request")
        )
        report, report_bytes, report_artifact, report_errors = self._read_ref(
            inputs.get("deterministic_report")
        )
        errors = [*question_errors, *answer_errors, *request_errors, *report_errors]
        if request is None:
            errors.append("deterministic_request_missing")
            request = {}
        if report is None:
            errors.append("deterministic_report_missing")
            report = {}
        activation_subject = {
            "question": inputs.get("question_subject"),
            "answer": inputs.get("answer_subject"),
            "subject_pair_sha256": (
                request.get("subject", {}).get("subject_pair_sha256")
                if isinstance(request.get("subject"), dict)
                else None
            ),
        }
        request_subject = request.get("subject")
        report_subject = report.get("subject")
        if not self._subjects_match(request_subject, activation_subject):
            errors.append("deterministic_request_subject_mismatch")
        if not self._subjects_match(report_subject, activation_subject):
            errors.append("deterministic_report_subject_mismatch")
        if report.get("request_sha256") != hashlib.sha256(request_bytes).hexdigest():
            errors.append("deterministic_report_request_hash_mismatch")
        atomic_scope = request.get("atomic_scope")
        report_scope = report.get("atomic_scope")
        if not isinstance(atomic_scope, dict) or atomic_scope != report_scope:
            errors.append("deterministic_atomic_scope_mismatch")
        report_errors_field = report.get("errors")
        if (
            report.get("passed") is not True
            or report.get("human_reviewed") is not False
            or report_errors_field != []
        ):
            errors.append("prefreeze_deterministic_qa_not_pass")
        reasons = _reason_codes(errors)
        artifact = report_artifact or request_artifact or None
        return (
            _slot(
                SLOTS[0],
                state="pass" if not errors else "blocked",
                verdict="pass" if not errors else "blocked",
                reasons=reasons,
                axes=["chemical", "numeric", "unit", "charge", "conservation"],
                artifact=artifact,
            ),
            {
                "inputs": inputs,
                "schemas": activation.get("schemas"),
                "request": request,
                "report": report,
                "request_artifact": request_artifact,
                "report_artifact": report_artifact,
            },
        )

    def _activation_paths(
        self, activation_ref: dict[str, Any], activation: dict[str, Any]
    ) -> tuple[dict[str, Path], dict[str, Any], list[str]]:
        errors: list[str] = []
        activation_relative = activation_ref.get("path")
        if not isinstance(activation_relative, str):
            return {}, {}, ["activation_reference_invalid"]
        try:
            activation_path = _checked_governance_path(self.workspace, activation_relative)
            dispatch_path = _checked_governance_path(
                self.workspace,
                str(Path(activation_relative).parent.as_posix())
                + "/review_dispatch_request_r18.json",
            )
            dispatch_bytes = dispatch_path.read_bytes()
            dispatch = _json_bytes(dispatch_bytes, "R18 review dispatch")
        except (OSError, ReadOnlyDataError):
            return {}, {}, ["review_dispatch_missing"]
        tasks = dispatch.get("requested_tasks")
        if not isinstance(tasks, list) or len(tasks) != 3:
            return {}, dispatch, ["review_dispatch_task_set_invalid"]
        activation_id = activation_path.parent.name
        expected_root = (
            self.workspace
            / "sh-chem-db/tests/generation_publication_v2/controller_fixture_r18/review_attempts"
            / activation_id
        ).absolute()
        result: dict[str, Path] = {}
        for task in tasks:
            if not isinstance(task, dict):
                errors.append("review_dispatch_task_invalid")
                continue
            slot = task.get("slot")
            raw = task.get("output_path")
            if slot not in {"sol_review_a", "sol_review_b", "adversarial_check"}:
                errors.append("review_dispatch_slot_set_invalid")
                continue
            if not isinstance(raw, str) or "\\" in raw or Path(raw).is_absolute():
                errors.append("review_output_path_invalid")
                continue
            parts = Path(raw).parts
            if any(part in {"", ".", ".."} for part in parts):
                errors.append("review_output_path_invalid")
                continue
            candidate = self.workspace.joinpath(*parts).absolute()
            expected = expected_root / f"{slot}.json"
            expected_relative = expected.relative_to(self.workspace).as_posix()
            # WindowsPath equality is case-insensitive.  Compare the canonical
            # contract spelling before touching a receipt so case mutations
            # cannot alias an activation-scoped output.
            if raw != expected_relative or candidate != expected:
                errors.append("legacy_or_non_activation_scoped_receipt")
                continue
            if slot in result:
                errors.append("review_dispatch_slot_ambiguous")
                continue
            result[slot] = candidate
        if set(result) != {"sol_review_a", "sol_review_b", "adversarial_check"}:
            errors.append("review_dispatch_slot_set_invalid")
        # The selector already verified the request.  Recheck that this exact
        # request still belongs to the selected activation snapshot.
        dispatch_core = activation.get("dispatch_core")
        if not isinstance(dispatch_core, dict):
            errors.append("review_dispatch_binding_missing")
        else:
            canonical = json.dumps(
                dispatch, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            if dispatch_core != {
                "canonical_sha256": hashlib.sha256(canonical).hexdigest(),
                "canonical_bytes": len(canonical),
            }:
                errors.append("review_dispatch_binding_mismatch")
        return result, dispatch, errors

    def _candidate_records(
        self,
        *,
        base_relative: str,
        filename: str,
    ) -> list[Path]:
        base = self.workspace / base_relative
        if not base.is_dir() or not _path_components_exact(self.workspace, base_relative):
            return []
        result: list[Path] = []
        try:
            packages = list(base.iterdir())
        except OSError:
            return []
        for package in packages:
            if not package.is_dir() or package.is_symlink():
                continue
            try:
                candidates = [entry for entry in package.iterdir() if entry.name == filename]
            except OSError:
                continue
            if len(candidates) == 1:
                candidate = candidates[0]
                if candidate.is_file() and not candidate.is_symlink():
                    result.append(candidate.absolute())
        return result

    def _select_support_record(
        self,
        *,
        kind: str,
        slot: str,
        activation_ref: dict[str, Any],
        receipt_path: Path,
        attestation_ref: dict[str, Any] | None = None,
    ) -> tuple[Path | None, dict[str, Any] | None, dict[str, Any] | None, list[str]]:
        if kind == "attestation":
            base = "staging/coordination/root/revisions/r18/review_task_attestations"
            filename = "review_task_creation_attestation_r18.json"
        else:
            base = "staging/coordination/root/revisions/r18/review_metadata_observations"
            filename = "review_codex_metadata_observation_r18.json"
        matches: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
        malformed_bound = False
        for path in self._candidate_records(base_relative=base, filename=filename):
            try:
                data = path.read_bytes()
                document = _json_bytes(data, "R18 review support record")
            except (OSError, ReadOnlyDataError):
                continue
            bound_activation = document.get("phase1_activation")
            if kind == "observation":
                task_ref = document.get("task_creation_attestation")
                bound = task_ref == attestation_ref
                receipt_ref = document.get("review_receipt")
                bound = bound and isinstance(receipt_ref, dict)
                bound = bound and receipt_ref.get("path") == receipt_path.relative_to(
                    self.workspace
                ).as_posix()
            else:
                bound = bound_activation == activation_ref
                bound = bound and document.get("allowed_output_path") == receipt_path.relative_to(
                    self.workspace
                ).as_posix()
            if not bound:
                continue
            if document.get("reviewer_slot") != slot:
                malformed_bound = True
                continue
            ref = {
                "path": path.relative_to(self.workspace).as_posix(),
                **_descriptor(data),
            }
            matches.append((path, document, ref))
        errors: list[str] = []
        if malformed_bound:
            errors.append(f"{kind}_slot_mismatch")
        if not matches:
            errors.append(f"{kind}_missing")
            return None, None, None, errors
        if len(matches) != 1:
            errors.append(f"{kind}_ambiguous")
            return None, None, None, errors
        path, document, reference = matches[0]
        return path, document, reference, errors

    def _validate_support_with_controller(
        self, kind: str, path: Path, slot: str
    ) -> list[str]:
        try:
            scripts = str((self.root / "scripts").absolute())
            if scripts not in sys.path:
                sys.path.insert(0, scripts)
            previous = sys.dont_write_bytecode
            sys.dont_write_bytecode = True
            try:
                from controller_v2.formal_freeze import (
                    validate_review_codex_metadata_observation,
                    validate_review_task_creation_attestation,
                )
            finally:
                sys.dont_write_bytecode = previous
            operation = (
                validate_review_task_creation_attestation
                if kind == "attestation"
                else validate_review_codex_metadata_observation
            )
            result = operation(self.root, path, expected_slot=slot)
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            return [f"{kind}_validation_unavailable"]
        if result.get("valid") is True:
            return []
        errors = result.get("errors")
        return list(errors) if isinstance(errors, list) else [f"{kind}_invalid"]

    def _review(
        self,
        public_slot: str,
        *,
        activation_ref: dict[str, Any],
        receipt_path: Path,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        slot = REVIEW_SLOT_NAMES[public_slot]
        errors: list[str] = []
        attestation_path, _, attestation_ref, attestation_errors = self._select_support_record(
            kind="attestation",
            slot=slot,
            activation_ref=activation_ref,
            receipt_path=receipt_path,
        )
        errors.extend(attestation_errors)
        if attestation_path is not None:
            errors.extend(
                self._validate_support_with_controller("attestation", attestation_path, slot)
            )
        observation_path: Path | None = None
        observation: dict[str, Any] | None = None
        if attestation_ref is not None:
            observation_path, observation, _, observation_errors = self._select_support_record(
                kind="observation",
                slot=slot,
                activation_ref=activation_ref,
                receipt_path=receipt_path,
                attestation_ref=attestation_ref,
            )
            errors.extend(observation_errors)
            if observation_path is not None:
                errors.extend(
                    self._validate_support_with_controller("observation", observation_path, slot)
                )
        if not receipt_path.is_file() or receipt_path.is_symlink():
            errors.append("activation_scoped_receipt_missing")
            return _slot(
                public_slot,
                state="missing" if not errors[:-1] else "blocked",
                verdict="missing",
                reasons=_reason_codes(errors) or ["receipt_missing"],
            )
        try:
            receipt_relative = receipt_path.relative_to(self.workspace).as_posix()
            receipt_path = _checked_governance_path(self.workspace, receipt_relative)
        except (OSError, ValueError, ReadOnlyDataError):
            errors.append("receipt_path_invalid")
            return _slot(
                public_slot,
                state="blocked",
                verdict="blocked",
                reasons=_reason_codes(errors),
            )
        try:
            receipt_bytes = receipt_path.read_bytes()
            receipt = _json_bytes(receipt_bytes, "R18 review receipt")
        except (OSError, ReadOnlyDataError):
            errors.append("receipt_invalid")
            return _slot(
                public_slot,
                state="blocked",
                verdict="blocked",
                reasons=_reason_codes(errors),
            )
        artifact = _descriptor(receipt_bytes)
        if observation is None or self._ref_identity(observation.get("review_receipt")) != (
            artifact["sha256"],
            artifact["bytes"],
        ):
            errors.append("observation_receipt_hash_bytes_mismatch")
        if receipt.get("slot") != slot:
            errors.append("receipt_slot_mismatch")
        if receipt.get("human_reviewed") is not False:
            errors.append("receipt_authority_invalid")
        if receipt.get("output_sha256") != _canonical_hash_without(
            receipt, "output_sha256"
        ):
            errors.append("receipt_output_sha256_mismatch")
        request = context.get("request") if isinstance(context.get("request"), dict) else {}
        if receipt.get("subject") != request.get("subject"):
            errors.append("receipt_subject_mismatch")
        report_artifact = context.get("report_artifact")
        expected_report_hash = (
            report_artifact.get("sha256") if isinstance(report_artifact, dict) else None
        )
        if receipt.get("deterministic_report_sha256") != expected_report_hash:
            errors.append("receipt_deterministic_report_hash_mismatch")
        verdict = receipt.get("verdict")
        if verdict not in {"pass", "fail"}:
            errors.append("receipt_verdict_invalid")
        findings_value = receipt.get("findings")
        if not isinstance(findings_value, list):
            errors.append("receipt_findings_invalid")
            findings_value = []
        schemas = context.get("schemas")
        schema_ref = (
            schemas.get("machine_review_record") if isinstance(schemas, dict) else None
        )
        schema, _, _, schema_errors = self._read_ref(schema_ref)
        errors.extend(f"receipt_schema:{item}" for item in schema_errors)
        if schema is None:
            errors.append("receipt_schema_missing")
        else:
            try:
                import jsonschema

                validator = jsonschema.Draft202012Validator(schema)
                if any(validator.iter_errors(receipt)):
                    errors.append("receipt_schema_invalid")
            except (ImportError, TypeError, ValueError):
                errors.append("receipt_schema_validation_unavailable")
        failed_ids: list[str] = []
        axes: set[str] = set()
        summaries: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        observed_ids: list[str] = []
        for finding in findings_value:
            if not isinstance(finding, dict):
                errors.append("receipt_finding_invalid")
                continue
            atomic_id = finding.get("atomic_part_id")
            if (
                not isinstance(atomic_id, str)
                or SAFE_ATOMIC_ID.fullmatch(atomic_id) is None
                or atomic_id in seen_ids
            ):
                errors.append("receipt_atomic_scope_invalid")
                continue
            seen_ids.add(atomic_id)
            observed_ids.append(atomic_id)
            if finding.get("verdict") != "fail":
                continue
            failed_ids.append(atomic_id)
            checks = finding.get("checks")
            if isinstance(checks, dict):
                for axis, value in checks.items():
                    if value == "fail" and isinstance(axis, str) and SAFE_AXIS.fullmatch(axis):
                        axes.add(axis)
            summary = _safe_summary(finding.get("finding"))
            if summary and len(summaries) < MAX_FINDINGS:
                summaries.append({"atomic_part_id": atomic_id, "summary": summary})
        atomic_scope = request.get("atomic_scope")
        expected_ids = (
            atomic_scope.get("declared_atomic_part_ids")
            if isinstance(atomic_scope, dict)
            else None
        )
        if not isinstance(expected_ids, list) or observed_ids != expected_ids:
            errors.append("receipt_atomic_scope_mismatch")
        if errors:
            return _slot(
                public_slot,
                state="blocked",
                verdict="blocked",
                reasons=_reason_codes(errors),
                artifact=artifact,
            )
        reasons = ["review_verdict_fail"] if verdict == "fail" else []
        return _slot(
            public_slot,
            state=str(verdict),
            verdict=str(verdict),
            reasons=reasons,
            failed_ids=failed_ids,
            axes=sorted(axes),
            findings=summaries,
            artifact=artifact,
        )

    def _registry_slots(self) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            path = _checked_governance_path(self.workspace, self.REGISTRY_PATH)
            data = path.read_bytes()
            registry = _json_bytes(data, "R18 governance registry")
        except (OSError, ReadOnlyDataError):
            return (
                _slot("chain", state="blocked", verdict="blocked", reasons=["registry_invalid"]),
                _slot(
                    "registration", state="blocked", verdict="blocked", reasons=["registry_invalid"]
                ),
            )
        artifact = _descriptor(data)
        chains = registry.get("chains")
        if not isinstance(chains, list):
            return (
                _slot("chain", state="blocked", verdict="blocked", reasons=["registry_invalid"], artifact=artifact),
                _slot("registration", state="blocked", verdict="blocked", reasons=["registry_invalid"], artifact=artifact),
            )
        if not chains:
            return (
                _slot("chain", state="absent", verdict="absent", reasons=["chain_absent"], artifact=artifact),
                _slot(
                    "registration",
                    state="absent",
                    verdict="absent",
                    reasons=["registration_absent"],
                    artifact=artifact,
                ),
            )
        # A non-empty registry needs full controller chain validation.  This
        # projection never guesses which row belongs to R18 from a filename or
        # untrusted ID, so an unproven/ambiguous registry remains blocked.
        return (
            _slot(
                "chain", state="blocked", verdict="blocked", reasons=["chain_selection_ambiguous"], artifact=artifact
            ),
            _slot(
                "registration", state="blocked", verdict="blocked", reasons=["registration_not_verified"], artifact=artifact
            ),
        )

    def current(self) -> dict[str, Any]:
        selection = self._selector_result()
        selector_errors = selection.get("errors")
        selector_reasons = _reason_codes(selector_errors)
        fallback_used = selection.get("legacy_fallback_used") is True
        selected = selection.get("selected")
        selection_valid = (
            selection.get("valid") is True
            and isinstance(selected, dict)
            and not fallback_used
        )
        activation_ref = selection.get("activation")
        activation: dict[str, Any] | None = None
        activation_artifact: dict[str, Any] | None = None
        activation_errors: list[str] = []
        if selection_valid:
            activation, _, activation_artifact, activation_errors = self._read_ref(
                activation_ref
            )
            if activation is None or activation_errors:
                selection_valid = False
                selector_reasons.extend(_reason_codes(activation_errors))
        if fallback_used:
            selection_valid = False
            selector_reasons.append("legacy_fallback_rejected")
        selector_reasons = list(dict.fromkeys(selector_reasons))[:MAX_REASON_CODES]
        if not selection_valid or activation is None or not isinstance(activation_ref, dict):
            if not selector_reasons:
                selector_reasons = ["active_selector_blocked"]
            blocked = [
                _slot(name, state="blocked", verdict="blocked", reasons=selector_reasons)
                for name in SLOTS[:3]
            ]
            blocked.extend(
                [
                    _slot(
                        "adversarial",
                        state="missing",
                        verdict="missing",
                        reasons=["adversarial_missing", "active_selector_blocked"],
                    ),
                    _slot(
                        "chain",
                        state="absent",
                        verdict="absent",
                        reasons=["chain_absent", "active_selector_blocked"],
                    ),
                    _slot(
                        "registration",
                        state="absent",
                        verdict="absent",
                        reasons=["registration_absent", "active_selector_blocked"],
                    ),
                ]
            )
            return {
                "schema_version": "r18_governance_projection_v1",
                "run_id": "r18",
                "active_selector": {
                    "state": "blocked",
                    "selected": False,
                    "legacy_fallback_used": False,
                    "reason_codes": selector_reasons,
                    "artifact": None,
                },
                "slots": blocked,
                "phase2_enabled": False,
                "download_available": False,
                "release": False,
                "registration_allowed": False,
                "read_only": True,
                "write_endpoints_present": False,
            }

        prefreeze, context = self._prefreeze(activation)
        paths, _, path_errors = self._activation_paths(activation_ref, activation)
        slots: list[dict[str, Any]] = [prefreeze]
        if path_errors or prefreeze["state"] != "pass":
            reasons = list(
                dict.fromkeys(
                    [*prefreeze["reason_codes"], *_reason_codes(path_errors)]
                )
            ) or ["prefreeze_or_dispatch_blocked"]
            slots.extend(
                [
                    _slot("review_a", state="blocked", verdict="blocked", reasons=reasons),
                    _slot("review_b", state="blocked", verdict="blocked", reasons=reasons),
                    _slot("adversarial", state="absent", verdict="absent", reasons=["adversarial_missing"]),
                ]
            )
        else:
            review_a = self._review(
                "review_a",
                activation_ref=activation_ref,
                receipt_path=paths["sol_review_a"],
                context=context,
            )
            review_b = self._review(
                "review_b",
                activation_ref=activation_ref,
                receipt_path=paths["sol_review_b"],
                context=context,
            )
            slots.extend([review_a, review_b])
            adversarial_path = paths["adversarial_check"]
            if not adversarial_path.is_file():
                adversarial = _slot(
                    "adversarial",
                    state="missing",
                    verdict="missing",
                    reasons=["adversarial_missing"],
                )
            else:
                # Phase 2 must have its controller sidecar and root observation;
                # a bare receipt can never unlock the UI.
                adversarial = _slot(
                    "adversarial",
                    state="blocked",
                    verdict="blocked",
                    reasons=["phase2_dispatch_not_verified"],
                    artifact=_descriptor(adversarial_path.read_bytes()),
                )
            slots.append(adversarial)
        chain, registration = self._registry_slots()
        slots.extend([chain, registration])
        return {
            "schema_version": "r18_governance_projection_v1",
            "run_id": "r18",
            "active_selector": {
                "state": "selected",
                "selected": True,
                "legacy_fallback_used": False,
                "reason_codes": [],
                "artifact": activation_artifact,
            },
            "slots": slots,
            "phase2_enabled": False,
            "download_available": False,
            "release": False,
            "registration_allowed": False,
            "read_only": True,
            "write_endpoints_present": False,
        }
