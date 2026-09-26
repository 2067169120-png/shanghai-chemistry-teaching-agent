from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from integrations.deeptutor_shchem_v1 import workbench_release_regression as module
from integrations.deeptutor_shchem_v1.config import ServingReleaseBinding
from integrations.deeptutor_shchem_v1.workbench_release_control import (
    WorkbenchReleaseControlError,
    WorkbenchReleaseStore,
    canonical_json_sha256,
)
from integrations.deeptutor_shchem_v1.workbench_release_gateway import (
    WorkbenchReleaseGateway,
)
from integrations.deeptutor_shchem_v1.workbench_release_regression import (
    FIXED_CHECK_IDS,
    WorkbenchReleaseRegressionError,
    WorkbenchReleaseRegressionRunner,
    WorkbenchReleaseRegressionVerifier,
)

WORKSPACE = Path(__file__).resolve().parents[4]
CONTRACT_ROOT = WORKSPACE / "sh-chem-db/kb/workbench/workbench_release_control_v1"


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def closure(seed: str = "release") -> dict[str, bytes]:
    return {
        "overlay/index.html": f"<main>{seed}</main>".encode(),
        "overlay/app.js": f"globalThis.seed={seed!r};".encode(),
        "api/theme-groups/master.json": json.dumps(
            {"scope": "master", "seed": seed}, sort_keys=True
        ).encode("utf-8"),
    }


def make_stack(tmp_path: Path):
    release_root = tmp_path / "external-release-store"
    verifier = WorkbenchReleaseRegressionVerifier(release_root, project_root=WORKSPACE)
    store = WorkbenchReleaseStore(
        release_root,
        project_root=WORKSPACE,
        pass_receipt_verifier=verifier,
        historical_replacement_receipt_verifier=(
            verifier.verify_historical_for_replacement_or_raise
        ),
    )
    runner = WorkbenchReleaseRegressionRunner(store)
    return verifier, store, runner


def fake_pass_step(
    step, sequence, project_root, cancel_event, *, candidate_root=None
):
    del candidate_root
    environment_names = {
        key.upper() for key in module._minimal_environment()
    }
    if step.check_id == "launcher_smoke":
        environment_names.add(module.CANDIDATE_ROOT_ENV)
    if cancel_event.is_set():
        return module._make_unexecuted_check(
            step,
            sequence,
            project_root,
            "cancelled",
            f"{step.check_id}_cancelled",
        )
    _, display, _ = module._fixed_command(step, project_root)
    now = module._utc_now()
    return module._with_self_hash(
        {
            "schema_version": module.CHECK_EVIDENCE_SCHEMA_VERSION,
            "check_id": step.check_id,
            "sequence": sequence,
            "implementation_id": step.implementation_id,
            "kind": step.kind,
            "actually_executed": True,
            "command": {
                "argv": display,
                "cwd": "<WORKSPACE>",
                "shell": False,
                "executable_sha256": "1" * 64,
                "executable_bytes": 1,
                "executable_path_sha256": "2" * 64,
                "environment_policy": module.ENVIRONMENT_POLICY,
                "environment_names": sorted(environment_names),
            },
            "status": "pass",
            "exit_code": 0,
            "started_at": now,
            "completed_at": now,
            "duration_ms": 0,
            "stdout": module._empty_stream(project_root),
            "stderr": module._empty_stream(project_root),
            "failure_code": None,
        }
    )


def test_runner_contract_is_pinned_strict_and_has_no_client_command_surface(
    tmp_path: Path,
) -> None:
    recipe_path = CONTRACT_ROOT / module.RUNNER_RECIPE_FILENAME
    schema_path = CONTRACT_ROOT / module.RUN_EVIDENCE_SCHEMA_FILENAME
    assert (
        sha256(recipe_path.read_bytes())
        == module.RUNNER_CONTRACT_FILE_SHA256[module.RUNNER_RECIPE_FILENAME]
    )
    assert (
        sha256(schema_path.read_bytes())
        == module.RUNNER_CONTRACT_FILE_SHA256[module.RUN_EVIDENCE_SCHEMA_FILENAME]
    )
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    assert recipe["execution_implemented"] is True
    assert recipe["client_supplied_commands_allowed"] is False
    assert recipe["api_key_access_allowed"] is False
    assert recipe["student_private_domain_access_allowed"] is False
    assert recipe["same_release_rerun_allowed"] is False
    assert [step["check_id"] for step in recipe["steps"]] == list(FIXED_CHECK_IDS)
    gateway_full = next(
        step for step in recipe["steps"] if step["check_id"] == "gateway_full_pytest"
    )
    assert gateway_full["timeout_seconds"] == 5400
    central_validate = next(
        step for step in recipe["steps"] if step["check_id"] == "central_validate"
    )
    assert central_validate["timeout_seconds"] == 600
    assert recipe["self_sha256"] == module.RUNNER_RECIPE_SELF_SHA256
    projected = dict(recipe)
    projected.pop("self_sha256")
    assert canonical_json_sha256(projected) == module.RUNNER_RECIPE_SELF_SHA256

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    _, store, runner = make_stack(tmp_path)
    candidate = store.freeze_candidate(closure("injection"))
    with pytest.raises(TypeError):
        runner.run(  # type: ignore[call-arg]
            candidate["release_id"],
            commands=[["cmd.exe", "/c", "whoami"]],
        )
    with pytest.raises(WorkbenchReleaseRegressionError) as malicious:
        runner.run(candidate["release_id"] + "; whoami")
    assert malicious.value.code == "regression_release_id_invalid"
    assert not list(runner.reservations_root.glob("*.json"))


def test_historical_runner_recipe_allowlist_is_replacement_only() -> None:
    current = (
        module.RUNNER_CONTRACT_FILE_SHA256[module.RUNNER_RECIPE_FILENAME],
        (CONTRACT_ROOT / module.RUNNER_RECIPE_FILENAME).stat().st_size,
        module.RUNNER_RECIPE_SELF_SHA256,
    )
    historical = next(iter(module.HISTORICAL_REPLACEMENT_RUNNER_RECIPE_BINDINGS))
    unknown = ("9" * 64, 2994, "8" * 64)

    assert module._trusted_runner_recipe_binding(
        claimed=current,
        evidence=current,
        current=current,
        fingerprint_mode=module._FingerprintTrustMode.CURRENT_RUNTIME,
    ) == current
    assert module._trusted_runner_recipe_binding(
        claimed=historical,
        evidence=historical,
        current=current,
        fingerprint_mode=(
            module._FingerprintTrustMode.HISTORICAL_FOR_REPLACEMENT_ONLY
        ),
    ) == historical

    with pytest.raises(WorkbenchReleaseRegressionError) as current_rejects_old:
        module._trusted_runner_recipe_binding(
            claimed=historical,
            evidence=historical,
            current=current,
            fingerprint_mode=module._FingerprintTrustMode.CURRENT_RUNTIME,
        )
    assert current_rejects_old.value.code == "regression_runner_recipe_binding_mismatch"

    with pytest.raises(WorkbenchReleaseRegressionError) as historical_rejects_unknown:
        module._trusted_runner_recipe_binding(
            claimed=unknown,
            evidence=unknown,
            current=current,
            fingerprint_mode=(
                module._FingerprintTrustMode.HISTORICAL_FOR_REPLACEMENT_ONLY
            ),
        )
    assert historical_rejects_unknown.value.code == "regression_historical_recipe_untrusted"

    with pytest.raises(WorkbenchReleaseRegressionError) as mismatch:
        module._trusted_runner_recipe_binding(
            claimed=historical,
            evidence=current,
            current=current,
            fingerprint_mode=(
                module._FingerprintTrustMode.HISTORICAL_FOR_REPLACEMENT_ONLY
            ),
        )
    assert mismatch.value.code == "regression_runner_recipe_binding_mismatch"


def test_real_runner_receipt_is_immutable_trusted_and_core_activatable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verifier, store, runner = make_stack(tmp_path)
    candidate = store.freeze_candidate(closure("trusted"))
    monkeypatch.setattr(module, "_execute_subprocess_step", fake_pass_step)

    result = runner.run(candidate["release_id"])
    receipt = result["receipt"]
    assert receipt["verdict"] == "PASS"
    assert [item["check_id"] for item in receipt["checks"]] == list(FIXED_CHECK_IDS)
    assert all(item["status"] == "pass" for item in receipt["checks"])
    assert result["run"]["automatic_activation"] is False
    assert result["run"]["api_key_accessed"] is False
    assert result["run"]["student_private_domain_accessed"] is False
    assert verifier(receipt) is True
    trusted = verifier.verify_or_raise(receipt)
    assert trusted["trusted"] is True
    assert trusted["release_id"] == candidate["release_id"]
    status = runner.read_result(receipt["receipt_id"])
    assert status["state"] == "completed"
    assert status["verdict"] == "PASS"
    assert status["trusted_pass_current"] is True
    assert status["cancellable"] is False
    assert [item["check_id"] for item in status["checks"]] == list(FIXED_CHECK_IDS)
    reopened = WorkbenchReleaseRegressionVerifier(
        store.release_root, project_root=WORKSPACE
    )
    restarted_status = reopened.status_for_release(candidate["release_id"])
    assert restarted_status["run_id"] == result["run"]["run_id"]
    assert restarted_status["trusted_pass_current"] is True
    assert reopened.retrieve_receipt(result["run"]["run_id"]) == receipt
    unstarted = store.freeze_candidate(closure("unstarted"))
    assert (
        reopened.status_for_release(unstarted["release_id"])["state"] == "not_started"
    )

    activated = store.activate_release(
        candidate["release_id"],
        receipt,
        expected_revision=None,
        principal_id="local-teacher",
        reason_zh="教师显式选择已完成固定回归的本地候选版本",
    )
    assert activated["active_pointer"]["release_id"] == candidate["release_id"]
    assert activated["automatic_activation"] is False

    for path in (
        runner.reservations_root / f"{candidate['release_id']}.json",
        runner.receipts_root / f"{receipt['receipt_id']}.json",
        runner.evidence_root / f"{receipt['receipt_id']}.json",
    ):
        module.release_control._assert_owner_only(path, directory=False)


def test_old_self_signed_forgery_omission_and_reordering_are_untrusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verifier, store, runner = make_stack(tmp_path)
    candidate = store.freeze_candidate(closure("forgery"))
    monkeypatch.setattr(module, "_execute_subprocess_step", fake_pass_step)
    real = runner.run(candidate["release_id"])["receipt"]

    self_signed = dict(real)
    self_signed["receipt_id"] = "WBRR-" + "a" * 32
    self_signed.pop("self_sha256")
    self_signed["self_sha256"] = canonical_json_sha256(self_signed)
    assert verifier(self_signed) is False

    missing = json.loads(json.dumps(real))
    missing["checks"].pop()
    missing.pop("self_sha256")
    missing["self_sha256"] = canonical_json_sha256(missing)
    assert verifier(missing) is False

    reordered = json.loads(json.dumps(real))
    reordered["checks"][0], reordered["checks"][1] = (
        reordered["checks"][1],
        reordered["checks"][0],
    )
    reordered.pop("self_sha256")
    reordered["self_sha256"] = canonical_json_sha256(reordered)
    assert verifier(reordered) is False

    with pytest.raises(WorkbenchReleaseControlError) as rejected:
        store.activate_release(
            candidate["release_id"],
            self_signed,
            expected_revision=None,
            principal_id="local-teacher",
            reason_zh="自签回执不得激活",
        )
    assert rejected.value.code in {
        "regression_receipt_untrusted",
        "regression_receipt_not_eligible",
    }
    assert store.read_active_pointer() is None


def test_historical_replacement_rejects_forgery_missing_issued_and_same_length_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verifier, store, runner = make_stack(tmp_path)
    candidate = store.freeze_candidate(closure("historical-attacks"))
    monkeypatch.setattr(module, "_execute_subprocess_step", fake_pass_step)
    real = runner.run(candidate["release_id"])["receipt"]

    projection = verifier.verify_historical_for_replacement_or_raise(real)
    assert projection["historical_for_replacement_only"] is True
    assert projection["serving_eligible"] is False
    assert projection["rollback_eligible"] is False

    forged = json.loads(json.dumps(real))
    forged["completed_at"] = "2026-08-26T12:34:56Z"
    forged.pop("self_sha256")
    forged["self_sha256"] = canonical_json_sha256(forged)
    with pytest.raises(WorkbenchReleaseRegressionError):
        verifier.verify_historical_for_replacement_or_raise(forged)

    issued_path = verifier.receipts_root / f"{real['receipt_id']}.json"
    issued_raw = issued_path.read_bytes()
    issued_path.unlink()
    with pytest.raises((WorkbenchReleaseRegressionError, WorkbenchReleaseControlError)):
        verifier.verify_historical_for_replacement_or_raise(real)
    module.release_control._write_exclusive(issued_path, issued_raw)

    tampered_raw = issued_raw.replace(b'"verdict":"PASS"', b'"verdict":"FAIL"', 1)
    assert tampered_raw != issued_raw
    assert len(tampered_raw) == len(issued_raw)
    issued_path.write_bytes(tampered_raw)
    with pytest.raises(WorkbenchReleaseRegressionError):
        verifier.verify_historical_for_replacement_or_raise(real)
    issued_path.write_bytes(issued_raw)
    assert verifier.verify_historical_for_replacement_or_raise(real)[
        "historical_for_replacement_only"
    ] is True


def test_gateway_replaces_real_old_generation_but_never_downgrades_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_workspace_fingerprint = {
        "algorithm": module.WORKSPACE_SOURCE_FINGERPRINT_ALGORITHM,
        "sha256": "1" * 64,
        "file_count": 101,
    }
    new_workspace_fingerprint = {
        "algorithm": module.WORKSPACE_SOURCE_FINGERPRINT_ALGORITHM,
        "sha256": "3" * 64,
        "file_count": 103,
    }
    monkeypatch.setattr(
        module,
        "_workspace_source_fingerprint",
        lambda _root: dict(old_workspace_fingerprint),
    )
    monkeypatch.setattr(module, "_runner_source_sha256", lambda: "2" * 64)
    monkeypatch.setattr(module, "_execute_subprocess_step", fake_pass_step)

    old_verifier, old_store, old_runner = make_stack(tmp_path)
    old_candidate = old_store.freeze_candidate(closure("old-generation"))
    old_result = old_runner.run(old_candidate["release_id"])
    old_head = old_store.activate_release(
        old_candidate["release_id"],
        old_result["receipt"],
        expected_revision=None,
        principal_id="local-teacher",
        reason_zh="建立旧代码代际的真实固定回归头",
    )["active_pointer"]
    assert old_verifier(old_result["receipt"]) is True

    monkeypatch.setattr(
        module,
        "_workspace_source_fingerprint",
        lambda _root: dict(new_workspace_fingerprint),
    )
    monkeypatch.setattr(module, "_runner_source_sha256", lambda: "4" * 64)
    serving = ServingReleaseBinding(
        mode="bootstrap_live",
        release_id=None,
        selected_revision=None,
        candidate_manifest_sha256=None,
        candidate_manifest_bytes=None,
        closure_sha256="5" * 64,
        browse_snapshot_id=None,
        ui_build_id="6" * 64,
        data_snapshot_id="7" * 64,
        backend_build_id=None,
    )
    gateway = WorkbenchReleaseGateway(
        WORKSPACE, old_store.release_root, serving
    )
    try:
        stale_status = gateway.status()
        serving_before = stale_status["serving_release"]
        assert stale_status["selected_release"]["revision"] == old_head["revision"]
        assert stale_status["selected_release_trust"] == {
            "state": "historical_for_replacement_only",
            "current_trusted": False,
            "replacement_trusted": True,
            "serving_eligible": False,
            "rollback_eligible": False,
            "replacement_only": True,
        }

        monkeypatch.setattr(
            gateway.materializer,
            "materialize",
            lambda: closure("new-generation"),
        )
        frozen = gateway.freeze_candidate()
        new_release_id = frozen["candidate"]["release_id"]
        started = gateway.start_regression(
            new_release_id, idempotency_key="upgrade-generation-0001"
        )
        run_id = started["run"]["run_id"]
        assert gateway._future is not None
        gateway._future.result(timeout=30)
        assert gateway.regression_status(new_release_id, run_id)["run"][
            "trusted_pass_current"
        ] is True

        selected = gateway.select_release(
            new_release_id,
            run_id,
            expected_revision=old_head["revision"],
            principal_id="local-teacher",
            reason_zh="以新代码代际固定回归安全替换旧头",
        )
        assert selected["serving_changed"] is False
        assert selected["selection_committed"] is True
        new_revision = selected["selected_release"]["revision"]
        selected_status = gateway.status()
        assert selected_status["selected_release_trust"]["current_trusted"] is True
        assert selected_status["serving_release"] == serving_before

        with pytest.raises(WorkbenchReleaseControlError) as rollback_rejected:
            gateway.rollback_release(
                old_candidate["release_id"],
                old_result["run"]["run_id"],
                expected_revision=new_revision,
                principal_id="local-teacher",
                reason_zh="旧代际回执不得因替换信任而获得回滚资格",
            )
        assert rollback_rejected.value.code == "regression_receipt_untrusted"
        assert gateway.status()["selected_release"]["release_id"] == new_release_id
    finally:
        gateway.shutdown()


def test_same_release_is_burned_and_single_inflight_rejects_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, store, runner = make_stack(tmp_path)
    first = store.freeze_candidate(closure("first"))
    second = store.freeze_candidate(closure("second"))
    entered = threading.Event()
    release = threading.Event()

    def blocking_pass(
        step, sequence, project_root, cancel_event, *, candidate_root=None
    ):
        if sequence == 1:
            entered.set()
            assert release.wait(timeout=10)
        return fake_pass_step(
            step,
            sequence,
            project_root,
            cancel_event,
            candidate_root=candidate_root,
        )

    monkeypatch.setattr(module, "_execute_subprocess_step", blocking_pass)
    result: dict[str, object] = {}

    def run_first() -> None:
        result.update(runner.run(first["release_id"]))

    thread = threading.Thread(target=run_first, daemon=True)
    thread.start()
    assert entered.wait(timeout=10)
    with pytest.raises(WorkbenchReleaseRegressionError) as concurrent:
        runner.run(second["release_id"])
    assert concurrent.value.code == "regression_run_in_progress"
    release.set()
    thread.join(timeout=20)
    assert result["receipt"]["verdict"] == "PASS"  # type: ignore[index]
    with pytest.raises(WorkbenchReleaseRegressionError) as repeated:
        runner.run(first["release_id"])
    assert repeated.value.code == "regression_release_already_attempted"


def test_cancel_fails_closed_persists_fail_and_cannot_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verifier, store, runner = make_stack(tmp_path)
    candidate = store.freeze_candidate(closure("cancel"))
    monkeypatch.setattr(module, "_execute_subprocess_step", fake_pass_step)
    cancelled = threading.Event()
    cancelled.set()
    result = runner.run(candidate["release_id"], cancel_event=cancelled)
    receipt = result["receipt"]
    assert receipt["verdict"] == "FAIL"
    assert receipt["checks"][0]["status"] == "fail"
    assert verifier(receipt) is False
    with pytest.raises(WorkbenchReleaseRegressionError) as repeated:
        runner.run(candidate["release_id"])
    assert repeated.value.code == "regression_release_already_attempted"
    assert store.read_active_pointer() is None


def test_timeout_and_output_explosion_are_killed_and_never_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = WORKSPACE
    python = Path(module.sys.executable).resolve(strict=True)

    timeout_step = module._StepSpec(
        "gateway_full_pytest", "timeout_test_v1", "subprocess", 1, 4096
    )
    monkeypatch.setattr(
        module,
        "_fixed_command",
        lambda step, root: (
            [str(python), "-B", "-c", "import time; time.sleep(5)"],
            ["<PYTHON_CURRENT>", "-B", "-c", "<FIXED_TIMEOUT_TEST>"],
            python,
        ),
    )
    timed = module._execute_subprocess_step(
        timeout_step, 1, project_root, threading.Event()
    )
    assert timed["status"] == "timeout"
    assert timed["failure_code"] == "gateway_full_pytest_timeout"
    assert timed["status"] != "pass"

    output_step = module._StepSpec(
        "gateway_full_pytest", "output_test_v1", "subprocess", 10, 1024
    )
    monkeypatch.setattr(
        module,
        "_fixed_command",
        lambda step, root: (
            [
                str(python),
                "-B",
                "-c",
                "import sys; sys.stdout.buffer.write(b'x'*1048576); sys.stdout.flush()",
            ],
            ["<PYTHON_CURRENT>", "-B", "-c", "<FIXED_OUTPUT_TEST>"],
            python,
        ),
    )
    exploded = module._execute_subprocess_step(
        output_step, 1, project_root, threading.Event()
    )
    assert exploded["status"] == "output_limit"
    assert exploded["stdout"]["truncated"] is True
    assert exploded["stdout"]["bytes_captured"] == 1024
    assert exploded["status"] != "pass"


def test_evidence_tamper_same_size_aba_and_candidate_postflight_drift_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verifier, store, runner = make_stack(tmp_path)
    candidate = store.freeze_candidate(closure("tamper"))
    monkeypatch.setattr(module, "_execute_subprocess_step", fake_pass_step)
    frozen_runner_source = module._runner_source_sha256()
    frozen_workspace_source = module._workspace_source_fingerprint(WORKSPACE)
    stable_runner_source = lambda: frozen_runner_source
    stable_workspace_source = lambda project_root: dict(frozen_workspace_source)
    monkeypatch.setattr(module, "_runner_source_sha256", stable_runner_source)
    monkeypatch.setattr(
        module, "_workspace_source_fingerprint", stable_workspace_source
    )
    result = runner.run(candidate["release_id"])
    receipt = result["receipt"]
    assert verifier(receipt) is True
    monkeypatch.setattr(module, "_runner_source_sha256", lambda: "e" * 64)
    assert verifier(receipt) is False
    monkeypatch.setattr(module, "_runner_source_sha256", stable_runner_source)

    def drifted_workspace_source(project_root):
        value = stable_workspace_source(project_root)
        return {**value, "sha256": "d" * 64}

    monkeypatch.setattr(
        module, "_workspace_source_fingerprint", drifted_workspace_source
    )
    assert verifier(receipt) is False
    monkeypatch.setattr(
        module, "_workspace_source_fingerprint", stable_workspace_source
    )
    evidence_path = runner.evidence_root / f"{receipt['receipt_id']}.json"
    original = evidence_path.read_bytes()
    mutated = bytearray(original)
    position = original.find(b'"runner_version":"1.0.0"')
    assert position >= 0
    mutated[position + len('"runner_version":"')] = ord("9")
    assert len(mutated) == len(original)
    evidence_path.write_bytes(bytes(mutated))
    module.release_control._apply_owner_only_permissions(evidence_path, directory=False)
    assert verifier(receipt) is False
    evidence_path.write_bytes(original)
    module.release_control._apply_owner_only_permissions(evidence_path, directory=False)
    assert verifier(receipt) is True

    # A second immutable release demonstrates postflight drift detection.  The
    # store itself normally prevents this; the controlled double models an ABA
    # observer returning a different manifest only on the final read.
    second = store.freeze_candidate(closure("postflight"))
    real_read = store.read_candidate
    calls = {"count": 0}

    def drifting_read(release_id):
        value = real_read(release_id)
        calls["count"] += 1
        if calls["count"] >= 3:
            changed = json.loads(json.dumps(value))
            changed["manifest_sha256"] = "f" * 64
            return changed
        return value

    monkeypatch.setattr(store, "read_candidate", drifting_read)
    drifted = runner.run(second["release_id"])
    assert drifted["receipt"]["verdict"] == "FAIL"
    closure_check = next(
        item
        for item in drifted["receipt"]["checks"]
        if item["check_id"] == "release_closure"
    )
    assert closure_check["status"] == "fail"
    assert verifier(drifted["receipt"]) is False


def test_safe_summary_redacts_keys_tokens_and_local_paths() -> None:
    raw = (
        f"Authorization: Bearer secret-value\napi_key=sk-supersecret\n{WORKSPACE}"
    ).encode()
    summary = module._safe_summary(raw, WORKSPACE)
    assert "secret-value" not in summary
    assert "sk-supersecret" not in summary
    assert str(WORKSPACE) not in summary
    assert "<REDACTED>" in summary
    assert "<WORKSPACE>" in summary
    environment = module._minimal_environment()
    assert not any(
        marker in key.upper()
        for key in environment
        for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD", "STUDENT")
    )
