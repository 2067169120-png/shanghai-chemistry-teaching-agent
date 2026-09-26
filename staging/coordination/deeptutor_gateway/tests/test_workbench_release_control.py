from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import uuid
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from integrations.deeptutor_shchem_v1 import workbench_release_control as module
from integrations.deeptutor_shchem_v1.workbench_release_control import (
    AUTHORITY,
    CONTRACT_FILE_SHA256,
    WorkbenchReleaseControlError,
    WorkbenchReleaseStore,
    canonical_json_sha256,
)


WORKSPACE = Path(__file__).resolve().parents[4]
CONTRACT_ROOT = (
    WORKSPACE / "sh-chem-db/kb/workbench/workbench_release_control_v1"
)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def closure(seed: str = "a") -> dict[str, bytes]:
    return {
        "overlay/index.html": f"<main>{seed}</main>".encode("utf-8"),
        "overlay/app.js": f"globalThis.releaseSeed={seed!r};".encode("utf-8"),
        "api/theme-groups/master.json": json.dumps(
            {"scope": "master", "seed": seed}, sort_keys=True
        ).encode("utf-8"),
    }


def make_store(
    tmp_path: Path, *, trusted_receipts: bool = True
) -> WorkbenchReleaseStore:
    return WorkbenchReleaseStore(
        tmp_path / "external-release-store",
        project_root=WORKSPACE,
        pass_receipt_verifier=(
            (lambda receipt: receipt["verdict"] == "PASS")
            if trusted_receipts
            else None
        ),
    )


def pass_receipt(
    store: WorkbenchReleaseStore,
    candidate: Mapping[str, object],
    *,
    receipt_id: str | None = None,
) -> dict[str, object]:
    checks = []
    for check_id in store.recipe_check_ids:
        evidence = f"evidence:{candidate['release_id']}:{check_id}".encode("utf-8")
        checks.append(
            {
                "check_id": check_id,
                "status": "pass",
                "evidence_sha256": sha256(evidence),
                "evidence_bytes": len(evidence),
            }
        )
    value: dict[str, object] = {
        "schema_version": "shchem.workbench.regression_receipt.v1",
        "receipt_id": receipt_id or "WBRR-" + uuid.uuid4().hex,
        "release_id": candidate["release_id"],
        "candidate_manifest_sha256": candidate["manifest_sha256"],
        "candidate_manifest_bytes": candidate["manifest_bytes"],
        "recipe_id": store.recipe_binding["recipe_id"],
        "recipe_sha256": store.recipe_binding["file_sha256"],
        "recipe_bytes": store.recipe_binding["file_bytes"],
        "verdict": "PASS",
        "checks": checks,
        "failures": [],
        "completed_at": "2026-08-26T12:00:00Z",
        "model_invoked": False,
        "human_reviewed": False,
        "teaching_use_allowed": False,
        "publication_allowed": False,
    }
    value["self_sha256"] = canonical_json_sha256(value)
    return value


def fail_receipt(
    store: WorkbenchReleaseStore, candidate: Mapping[str, object]
) -> dict[str, object]:
    value = pass_receipt(store, candidate)
    value["verdict"] = "FAIL"
    value["checks"][0]["status"] = "fail"  # type: ignore[index]
    value["failures"] = ["gateway_full_pytest_failed"]
    value.pop("self_sha256")
    value["self_sha256"] = canonical_json_sha256(value)
    return value


def candidate_artifact_path(
    store: WorkbenchReleaseStore, candidate: Mapping[str, object], relative: str
) -> Path:
    return (
        store.candidates_root
        / str(candidate["release_id"])
        / "browse-closure"
        / Path(*relative.split("/"))
    )


def test_contracts_are_draft202012_byte_pinned_and_recipe_is_explicitly_nonexecuting(
    tmp_path: Path,
) -> None:
    for filename, expected in CONTRACT_FILE_SHA256.items():
        path = CONTRACT_ROOT / filename
        assert sha256(path.read_bytes()) == expected
        if filename.endswith(".schema.json"):
            schema = json.loads(path.read_text(encoding="utf-8"))
            assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
            Draft202012Validator.check_schema(schema)

    store = make_store(tmp_path)
    recipe = json.loads(
        (CONTRACT_ROOT / "release_recipe.json").read_text(encoding="utf-8")
    )
    assert recipe["execution_implemented"] is False
    assert recipe["automatic_activation"] is False
    assert recipe["client_supplied_commands_allowed"] is False
    assert recipe["human_review_claim_allowed"] is False
    assert recipe["teaching_use_allowed"] is False
    assert recipe["publication_allowed"] is False
    assert set(store.recipe_check_ids) == {
        "gateway_full_pytest",
        "central_status",
        "central_validate",
        "node_check_overlay",
        "openapi_contract",
        "release_closure",
        "launcher_smoke",
    }


def test_store_must_be_external_and_owner_only(tmp_path: Path) -> None:
    with pytest.raises(WorkbenchReleaseControlError) as captured:
        WorkbenchReleaseStore(
            WORKSPACE / "runtime/forbidden-release-store",
            project_root=WORKSPACE,
        )
    assert captured.value.code == "release_store_not_external"

    fake_project = tmp_path / "fake-project"
    fake_project.mkdir()
    with pytest.raises(WorkbenchReleaseControlError) as spoofed:
        WorkbenchReleaseStore(
            tmp_path / "spoofed-external-store",
            project_root=fake_project,
        )
    assert spoofed.value.code == "release_project_root_identity_mismatch"

    store = make_store(tmp_path)
    for directory in (
        store.release_root,
        store.candidates_root,
        store.receipts_root,
        store.events_root,
    ):
        module._assert_owner_only(directory, directory=True)
        if os.name != "nt":
            assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    with store._locked():
        module._assert_owner_only(store.lock_path, directory=False)
        if os.name != "nt":
            assert stat.S_IMODE(store.lock_path.stat().st_mode) == 0o600

    try:
        if os.name == "nt":
            subprocess.run(
                ["icacls", str(store.release_root), "/grant", "*S-1-1-0:(F)"],
                check=True,
                capture_output=True,
            )
        else:
            store.release_root.chmod(0o755)
        with pytest.raises(WorkbenchReleaseControlError) as broad:
            module._assert_owner_only(store.release_root, directory=True)
        assert broad.value.code == "release_store_permissions_broad"
    finally:
        module._apply_owner_only_permissions(store.release_root, directory=True)
        module._assert_owner_only(store.release_root, directory=True)


def test_freeze_is_content_addressed_idempotent_and_does_not_modify_source(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    source = tmp_path / "source.bin"
    source.write_bytes(b"immutable-source-evidence")
    before = (source.stat().st_size, sha256(source.read_bytes()))
    materialized = closure("first")
    materialized["source/evidence.bin"] = source.read_bytes()

    first = store.freeze_candidate(materialized)
    second = store.freeze_candidate(materialized)
    third = store.freeze_candidate(closure("second"))

    assert first["release_id"].startswith("WBREL-")
    assert len(first["release_id"]) == len("WBREL-") + 64
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert second["release_id"] == first["release_id"]
    assert third["release_id"] != first["release_id"]
    assert len(store.list_candidates()) == 2
    assert store.read_active_pointer() is None
    assert store.list_activation_history() == []
    assert (source.stat().st_size, sha256(source.read_bytes())) == before
    assert first["manifest"]["authority"] == AUTHORITY
    assert first["authority"] == AUTHORITY
    assert all(value is False for key, value in AUTHORITY.items() if key not in {"scope", "candidate_only", "read_only"})

    schema = json.loads(
        (CONTRACT_ROOT / "release_candidate.schema.json").read_text(encoding="utf-8")
    )
    assert list(Draft202012Validator(schema).iter_errors(first["manifest"])) == []


@pytest.mark.parametrize(
    ("bad_path", "bad_value"),
    [
        ("../escape", b"x"),
        ("/absolute", b"x"),
        (r"windows\escape", b"x"),
        ("safe/../../escape", b"x"),
        ("CON/file", b"x"),
        ("safe/file", b""),
        ("safe/file", bytearray(b"mutable")),
    ],
)
def test_invalid_closure_paths_and_mutable_or_empty_values_are_rejected(
    tmp_path: Path, bad_path: str, bad_value: object
) -> None:
    store = make_store(tmp_path)
    with pytest.raises(WorkbenchReleaseControlError):
        store.freeze_candidate({bad_path: bad_value})  # type: ignore[dict-item]
    assert store.list_candidates() == []


def test_case_insensitive_path_collision_is_rejected(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with pytest.raises(WorkbenchReleaseControlError) as captured:
        store.freeze_candidate({"Case/File": b"one", "case/file": b"two"})
    assert captured.value.code == "release_closure_path_duplicate"
    assert store.list_candidates() == []


class SameSizeChangingMapping(Mapping[str, bytes]):
    def __init__(self) -> None:
        self.reads = 0

    def __getitem__(self, key: str) -> bytes:
        assert key == "overlay/index.html"
        self.reads += 1
        return b"AAAA" if self.reads == 1 else b"BBBB"

    def __iter__(self) -> Iterator[str]:
        yield "overlay/index.html"

    def __len__(self) -> int:
        return 1


def test_closure_cas_and_same_size_change_fail_without_a_selectable_candidate(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    stable = closure()
    with pytest.raises(WorkbenchReleaseControlError) as cas:
        store.freeze_candidate(stable, expected_closure_sha256="0" * 64)
    assert cas.value.code == "release_closure_cas_mismatch"

    with pytest.raises(WorkbenchReleaseControlError) as changed:
        store.freeze_candidate(SameSizeChangingMapping())
    assert changed.value.code == "release_closure_changed"
    assert store.list_candidates() == []


def test_partial_damaged_missing_rogue_and_hardlinked_candidates_are_invisible(
    tmp_path: Path,
) -> None:
    partial_store = make_store(tmp_path / "partial")
    (partial_store.candidates_root / ".pending-deadbeef").mkdir()
    fake_id = "WBREL-" + "a" * 64
    (partial_store.candidates_root / fake_id).mkdir()
    assert partial_store.list_candidates() == []

    missing_store = make_store(tmp_path / "missing")
    missing = missing_store.freeze_candidate(closure("missing"))
    (
        missing_store.candidates_root
        / missing["release_id"]
        / "release.committed.json"
    ).unlink()
    assert missing_store.list_candidates() == []
    with pytest.raises(WorkbenchReleaseControlError):
        missing_store.read_candidate(missing["release_id"])

    rogue_store = make_store(tmp_path / "rogue")
    rogue = rogue_store.freeze_candidate(closure("rogue"))
    empty_rogue = rogue_store.candidates_root / rogue["release_id"] / "empty-rogue"
    module._mkdir_secure(empty_rogue)
    assert rogue_store.list_candidates() == []
    empty_rogue.rmdir()
    rogue_path = rogue_store.candidates_root / rogue["release_id"] / "rogue.bin"
    module._write_exclusive(rogue_path, b"rogue")
    assert rogue_store.list_candidates() == []
    with pytest.raises(WorkbenchReleaseControlError) as rogue_error:
        rogue_store.read_candidate(rogue["release_id"])
    assert rogue_error.value.code == "release_candidate_inventory_mismatch"

    link_store = make_store(tmp_path / "link")
    linked = link_store.freeze_candidate(closure("link"))
    artifact = candidate_artifact_path(link_store, linked, "overlay/index.html")
    hardlink = artifact.with_name("index-hardlink.html")
    os.link(artifact, hardlink)
    assert link_store.list_candidates() == []
    with pytest.raises(WorkbenchReleaseControlError) as hardlink_error:
        link_store.read_candidate(linked["release_id"])
    assert hardlink_error.value.code in {
        "release_store_file_unsafe",
        "release_store_path_unsafe",
    }


def test_concurrent_identical_freezes_commit_exactly_one_candidate(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    materialized = closure("concurrent")
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: store.freeze_candidate(materialized), range(8)))
    assert len({result["release_id"] for result in results}) == 1
    assert sum(result["idempotent"] is False for result in results) == 1
    assert sum(result["idempotent"] is True for result in results) == 7
    assert len(store.list_candidates()) == 1


def test_candidate_commit_is_complete_before_candidate_becomes_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    real_move = module._durable_move_no_replace
    observations: list[tuple[bool, set[str]]] = []

    def inspect_move(source: Path, target: Path) -> None:
        if source.is_dir() and target.parent == store.candidates_root:
            files, _ = module._tree_inventory(source)
            observations.append(
                ((source / "release.committed.json").is_file(), files)
            )
        real_move(source, target)

    monkeypatch.setattr(module, "_durable_move_no_replace", inspect_move)
    candidate = store.freeze_candidate(closure("commit-before-rename"))
    assert observations == [
        (
            True,
            {
                "release.manifest.json",
                "release.committed.json",
                "browse-closure/overlay/index.html",
                "browse-closure/overlay/app.js",
                "browse-closure/api/theme-groups/master.json",
            },
        )
    ]
    assert store.read_candidate(candidate["release_id"])["release_id"] == candidate[
        "release_id"
    ]


def test_activation_requires_an_explicit_trusted_pass_receipt_verifier(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path, trusted_receipts=False)
    candidate = store.freeze_candidate(closure("untrusted-receipt"))
    with pytest.raises(WorkbenchReleaseControlError) as unavailable:
        store.activate_release(
            candidate["release_id"],
            pass_receipt(store, candidate),
            expected_revision=None,
            principal_id="local-teacher",
            reason_zh="没有可信固定运行器不得激活",
        )
    assert unavailable.value.code == "regression_receipt_verifier_unavailable"
    assert store.read_active_pointer() is None
    assert list(store.events_root.glob("*.json")) == []


def test_freeze_and_pass_receipt_never_autoactivate_and_fail_receipt_is_rejected(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    candidate = store.freeze_candidate(closure("activation"))
    passing = pass_receipt(store, candidate)
    failing = fail_receipt(store, candidate)

    assert store.read_active_pointer() is None
    assert not list(store.receipts_root.glob("*.json"))
    with pytest.raises(WorkbenchReleaseControlError) as rejected:
        store.activate_release(
            candidate["release_id"],
            failing,
            expected_revision=None,
            principal_id="local-teacher",
            reason_zh="失败回执不得激活",
        )
    assert rejected.value.code in {
        "release_contract_validation_failed",
        "regression_receipt_not_eligible",
    }
    assert store.read_active_pointer() is None

    activated = store.activate_release(
        candidate["release_id"],
        passing,
        expected_revision=None,
        principal_id="local-teacher",
        reason_zh="显式选择本地候选题库版本",
    )
    pointer = store.read_active_pointer()
    assert pointer == activated["active_pointer"]
    assert pointer["release_id"] == candidate["release_id"]
    assert pointer["action"] == "activate"
    assert pointer["authority"] == AUTHORITY
    assert activated["automatic_activation"] is False
    assert activated["teaching_use_allowed"] is False
    assert activated["publication_allowed"] is False
    assert len(store.list_activation_history()) == 1


def test_new_trusted_release_can_cas_replace_a_structurally_valid_stale_head(
    tmp_path: Path,
) -> None:
    initial_store = make_store(tmp_path)
    first = initial_store.freeze_candidate(closure("old-source-generation"))
    first_head = initial_store.activate_release(
        first["release_id"],
        pass_receipt(initial_store, first),
        expected_revision=None,
        principal_id="local-teacher",
        reason_zh="选择旧源代码代际候选",
    )["active_pointer"]
    second = initial_store.freeze_candidate(closure("new-source-generation"))

    # Simulate a new runner generation: the historical receipt remains
    # structurally bound but is no longer current-trusted; only the new
    # candidate receipt is trusted.
    upgraded_store = WorkbenchReleaseStore(
        initial_store.release_root,
        project_root=WORKSPACE,
        pass_receipt_verifier=(
            lambda receipt: receipt["release_id"] == second["release_id"]
        ),
        historical_replacement_receipt_verifier=(
            lambda receipt: {
                "historical_for_replacement_only": True,
                "current_trusted": False,
                "serving_eligible": False,
                "rollback_eligible": False,
                "receipt_id": receipt["receipt_id"],
                "release_id": receipt["release_id"],
            }
        ),
    )
    with pytest.raises(WorkbenchReleaseControlError) as stale:
        upgraded_store.read_active_pointer()
    assert stale.value.code == "regression_receipt_untrusted"

    replacement_head = upgraded_store.read_active_pointer_for_replacement()
    assert replacement_head == first_head
    activated = upgraded_store.activate_release(
        second["release_id"],
        pass_receipt(upgraded_store, second),
        expected_revision=replacement_head["revision"],
        principal_id="local-teacher",
        reason_zh="以当前可信回归候选替换旧源代码代际",
    )["active_pointer"]
    assert activated["release_id"] == second["release_id"]
    assert upgraded_store.read_active_pointer() == activated
    assert len(upgraded_store.list_activation_history()) == 2


def test_real_historical_rollback_selects_immutable_bytes_and_random_cas_blocks_aba(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    first = store.freeze_candidate(closure("first"))
    second = store.freeze_candidate(closure("second"))
    never_active = store.freeze_candidate(closure("never-active"))
    first_receipt = pass_receipt(store, first)
    second_receipt = pass_receipt(store, second)
    never_receipt = pass_receipt(store, never_active)

    first_activation = store.activate_release(
        first["release_id"],
        first_receipt,
        expected_revision=None,
        principal_id="local-teacher",
        reason_zh="激活第一版",
    )
    revision_one = first_activation["active_pointer"]["revision"]
    second_activation = store.activate_release(
        second["release_id"],
        second_receipt,
        expected_revision=revision_one,
        principal_id="local-teacher",
        reason_zh="激活第二版",
    )
    revision_two = second_activation["active_pointer"]["revision"]

    with pytest.raises(WorkbenchReleaseControlError) as not_historical:
        store.rollback_to_release(
            never_active["release_id"],
            never_receipt,
            expected_revision=revision_two,
            principal_id="local-teacher",
            reason_zh="不得回退到从未激活版本",
        )
    assert not_historical.value.code == "rollback_target_not_historical"

    first_bytes_before = candidate_artifact_path(
        store, first, "overlay/index.html"
    ).read_bytes()
    rolled_back = store.rollback_to_release(
        first["release_id"],
        first_receipt,
        expected_revision=revision_two,
        principal_id="local-teacher",
        reason_zh="第二版异常，回到历史第一版",
    )
    revision_three = rolled_back["active_pointer"]["revision"]
    assert len({revision_one, revision_two, revision_three}) == 3
    assert rolled_back["active_pointer"]["release_id"] == first["release_id"]
    assert rolled_back["active_pointer"]["action"] == "rollback"
    assert candidate_artifact_path(
        store, first, "overlay/index.html"
    ).read_bytes() == first_bytes_before
    assert store.read_candidate(first["release_id"])["manifest_sha256"] == first[
        "manifest_sha256"
    ]
    assert [event["release_id"] for event in store.list_activation_history()] == [
        first["release_id"],
        second["release_id"],
        first["release_id"],
    ]

    with pytest.raises(WorkbenchReleaseControlError) as stale:
        store.activate_release(
            second["release_id"],
            second_receipt,
            expected_revision=revision_one,
            principal_id="local-teacher",
            reason_zh="旧页面提交必须失败",
        )
    assert stale.value.code == "active_revision_conflict"
    assert store.read_active_pointer()["revision"] == revision_three


def test_rollback_rejects_recreated_release_id_with_different_historical_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    first_closure = closure("historical-bytes")
    first = store.freeze_candidate(first_closure)
    second = store.freeze_candidate(closure("current-bytes"))
    first_head = store.activate_release(
        first["release_id"],
        pass_receipt(store, first),
        expected_revision=None,
        principal_id="local-teacher",
        reason_zh="建立第一份历史字节",
    )["active_pointer"]
    second_head = store.activate_release(
        second["release_id"],
        pass_receipt(store, second),
        expected_revision=first_head["revision"],
        principal_id="local-teacher",
        reason_zh="切换到第二份字节",
    )["active_pointer"]

    shutil.rmtree(store.candidates_root / first["release_id"])
    monkeypatch.setattr(module, "_utc_now", lambda: "2026-08-26T13:00:00Z")
    recreated = store.freeze_candidate(first_closure)
    assert recreated["release_id"] == first["release_id"]
    assert recreated["manifest_sha256"] != first["manifest_sha256"]
    with pytest.raises(WorkbenchReleaseControlError) as mismatch:
        store.rollback_to_release(
            recreated["release_id"],
            pass_receipt(store, recreated),
            expected_revision=second_head["revision"],
            principal_id="local-teacher",
            reason_zh="相同标签但不同历史清单不得回退",
        )
    assert mismatch.value.code == "rollback_candidate_binding_mismatch"
    assert store.read_active_pointer() == second_head


def test_revision_randomness_collision_cannot_recreate_an_old_cas_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    first = store.freeze_candidate(closure("revision-first"))
    second = store.freeze_candidate(closure("revision-second"))
    first_head = store.activate_release(
        first["release_id"],
        pass_receipt(store, first),
        expected_revision=None,
        principal_id="local-teacher",
        reason_zh="建立不可复用的CAS版本",
    )["active_pointer"]
    colliding_hex = first_head["revision"].removeprefix("WBREV-")
    monkeypatch.setattr(module.secrets, "token_hex", lambda _: colliding_hex)
    with pytest.raises(WorkbenchReleaseControlError) as exhausted:
        store.activate_release(
            second["release_id"],
            pass_receipt(store, second),
            expected_revision=first_head["revision"],
            principal_id="local-teacher",
            reason_zh="随机值碰撞不得制造ABA",
        )
    assert exhausted.value.code == "active_revision_identity_exhausted"
    assert store.read_active_pointer() == first_head


def test_concurrent_activation_cas_allows_only_one_new_head(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    base = store.freeze_candidate(closure("base"))
    left = store.freeze_candidate(closure("left"))
    right = store.freeze_candidate(closure("right"))
    base_activation = store.activate_release(
        base["release_id"],
        pass_receipt(store, base),
        expected_revision=None,
        principal_id="local-teacher",
        reason_zh="建立初始版本",
    )
    expected = base_activation["active_pointer"]["revision"]

    def activate(candidate: Mapping[str, object]) -> str:
        try:
            store.activate_release(
                str(candidate["release_id"]),
                pass_receipt(store, candidate),
                expected_revision=expected,
                principal_id="local-teacher",
                reason_zh="并发激活竞争",
            )
            return "success"
        except WorkbenchReleaseControlError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(activate, (left, right)))
    assert sorted(outcomes) == ["active_revision_conflict", "success"]
    assert store.read_active_pointer()["release_id"] in {
        left["release_id"],
        right["release_id"],
    }
    assert len(store.list_activation_history()) == 2


def test_pointer_postwrite_failure_restores_real_previous_release_and_event_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    first = store.freeze_candidate(closure("stable"))
    second = store.freeze_candidate(closure("rejected"))
    first_activation = store.activate_release(
        first["release_id"],
        pass_receipt(store, first),
        expected_revision=None,
        principal_id="local-teacher",
        reason_zh="稳定版本",
    )
    previous = first_activation["active_pointer"]
    real_read = module._read_file_stable
    injected = {"done": False}

    def one_bad_new_pointer_read(path: Path, **kwargs: object) -> bytes:
        raw = real_read(path, **kwargs)
        if (
            Path(path) == store.active_pointer_path
            and not injected["done"]
            and second["release_id"].encode("ascii") in raw
        ):
            injected["done"] = True
            return b"same-size-postwrite-drift"
        return raw

    monkeypatch.setattr(module, "_read_file_stable", one_bad_new_pointer_read)
    with pytest.raises(WorkbenchReleaseControlError) as failed:
        store.activate_release(
            second["release_id"],
            pass_receipt(store, second),
            expected_revision=previous["revision"],
            principal_id="local-teacher",
            reason_zh="注入指针提交后漂移",
        )
    assert failed.value.code == "active_pointer_postwrite_mismatch"
    monkeypatch.setattr(module, "_read_file_stable", real_read)

    restored = store.read_active_pointer()
    assert restored == previous
    assert len(store.list_activation_history()) == 1
    assert not any(
        second["release_id"] in path.read_text(encoding="utf-8")
        for path in store.events_root.glob("*.json")
    )


def test_active_pointer_fails_closed_when_real_candidate_bytes_are_tampered(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    candidate = store.freeze_candidate(closure("tamper"))
    store.activate_release(
        candidate["release_id"],
        pass_receipt(store, candidate),
        expected_revision=None,
        principal_id="local-teacher",
        reason_zh="建立待篡改版本",
    )
    artifact = candidate_artifact_path(store, candidate, "overlay/index.html")
    original = artifact.read_bytes()
    assert len(original) > 1
    artifact.write_bytes(b"X" * len(original))
    module._apply_owner_only_permissions(artifact, directory=False)

    with pytest.raises(WorkbenchReleaseControlError) as captured:
        store.read_active_pointer()
    assert captured.value.code in {
        "release_candidate_artifact_mismatch",
        "release_candidate_drift",
    }
    assert store.list_candidates() == []


def test_dependency_failure_after_pointer_write_restores_previous_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    first = store.freeze_candidate(closure("dependency-stable"))
    second = store.freeze_candidate(closure("dependency-rejected"))
    initial = store.activate_release(
        first["release_id"],
        pass_receipt(store, first),
        expected_revision=None,
        principal_id="local-teacher",
        reason_zh="依赖复读前的稳定版本",
    )["active_pointer"]
    real_read_pointer = store._read_active_pointer_unlocked
    injected = {"done": False}

    def fail_new_dependency_read(*, verify_dependencies: bool):
        value = real_read_pointer(verify_dependencies=verify_dependencies)
        if (
            verify_dependencies
            and value is not None
            and value["release_id"] == second["release_id"]
            and not injected["done"]
        ):
            injected["done"] = True
            raise WorkbenchReleaseControlError(
                "injected_dependency_failure", "injected dependency failure"
            )
        return value

    monkeypatch.setattr(store, "_read_active_pointer_unlocked", fail_new_dependency_read)
    with pytest.raises(WorkbenchReleaseControlError) as captured:
        store.activate_release(
            second["release_id"],
            pass_receipt(store, second),
            expected_revision=initial["revision"],
            principal_id="local-teacher",
            reason_zh="提交后依赖复读失败",
        )
    assert captured.value.code == "injected_dependency_failure"
    monkeypatch.setattr(store, "_read_active_pointer_unlocked", real_read_pointer)
    assert store.read_active_pointer() == initial
    assert len(store.list_activation_history()) == 1
