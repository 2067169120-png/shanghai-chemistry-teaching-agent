from __future__ import annotations

import json
import os
import stat
import threading
import traceback
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import integrations.deeptutor_shchem_v1.model_provider_settings as settings_module
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    CREDENTIAL_TARGET_PREFIX,
    DEFAULT_PROVIDER_POLICIES,
    LOCK_FILE_NAME,
    SETTINGS_FILE_NAME,
    CredentialBackendUnavailable,
    ModelPolicy,
    ModelProviderSettingsError,
    ModelProviderSettingsStore,
    ProviderPolicy,
    WindowsCredentialManagerBackend,
)

SECRET_ONE = "sk-test-only-NOT-A-REAL-KEY-123456"
SECRET_TWO = "sk-test-only-NOT-A-REAL-KEY-rotated"


class FakeCredentialBackend:
    def __init__(self, *, available: bool = True) -> None:
        self.is_available = available
        self.values: dict[str, str] = {}
        self.write_calls: list[str] = []
        self.exists_calls: list[str] = []
        self.read_calls: list[str] = []
        self.delete_calls: list[str] = []
        self.fail_write_with_secret = False

    def available(self) -> bool:
        return self.is_available

    def write(self, target_name: str, secret: str) -> None:
        self.write_calls.append(target_name)
        if self.fail_write_with_secret:
            raise RuntimeError(f"backend leaked {secret}")
        self.values[target_name] = secret

    def read(self, target_name: str) -> str | None:
        self.read_calls.append(target_name)
        return self.values.get(target_name)

    def exists(self, target_name: str) -> bool:
        self.exists_calls.append(target_name)
        return target_name in self.values

    def delete(self, target_name: str) -> bool:
        self.delete_calls.append(target_name)
        return self.values.pop(target_name, None) is not None


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    project_root = tmp_path / "project"
    metadata_root = tmp_path / "external-user-state"
    project_root.mkdir()
    return project_root, metadata_root


@pytest.fixture
def backend() -> FakeCredentialBackend:
    return FakeCredentialBackend()


@pytest.fixture
def store(
    roots: tuple[Path, Path], backend: FakeCredentialBackend
) -> ModelProviderSettingsStore:
    project_root, metadata_root = roots
    return ModelProviderSettingsStore(
        metadata_root,
        project_root=project_root,
        credential_backend=backend,
    )


def profile(
    profile_id: str = "teacher-default",
    **overrides: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "profile_id": profile_id,
        "provider_id": "openai",
        "model_id": "gpt-5-mini",
        "base_url_policy": "openai_official_https_v1",
        "allowed_data_classes": ["synthetic_only", "question_text_redacted"],
        "image_egress": "deny",
        "last_probe": {
            "status": "never",
            "checked_at": None,
            "error_code": None,
            "model_invoked": False,
        },
    }
    value.update(overrides)
    return value


def create_profile(store: ModelProviderSettingsStore) -> dict[str, object]:
    return store.upsert_metadata(profile(), expected_revision=None)


@pytest.mark.parametrize("limits", [
    {}, {"max_input_tokens": None, "max_output_tokens": None},
    {"max_input_tokens": 1}, {"max_output_tokens": 1},
    {"max_input_tokens": 64000, "max_output_tokens": 65536},
    {"max_input_tokens": 1_000_000, "max_output_tokens": 1_000_000},
])
def test_user_token_limits_roundtrip_without_credential_access(
    store: ModelProviderSettingsStore,
    roots: tuple[Path, Path],
    backend: FakeCredentialBackend,
    limits: dict[str, int | None],
) -> None:
    created = store.upsert_metadata(profile(**limits), expected_revision=None)
    reloaded = ModelProviderSettingsStore(
        roots[1], project_root=roots[0], credential_backend=backend
    )
    listed = reloaded.list_metadata()[0]
    policy = reloaded.invocation_policy(
        "teacher-default", expected_revision=str(created["revision"])
    )
    stored = json.loads((roots[1] / SETTINGS_FILE_NAME).read_text(encoding="utf-8"))
    for field in ("max_input_tokens", "max_output_tokens"):
        for value in (created, listed, policy, stored["profiles"][0]):
            assert value[field] == limits.get(field)
    assert listed["revision"] == created["revision"]
    assert not backend.read_calls
    assert not backend.write_calls
    assert not backend.delete_calls


@pytest.mark.parametrize("field", ["max_input_tokens", "max_output_tokens"])
@pytest.mark.parametrize("invalid", [True, False, 0, -1, 1.0, 8000.5, "8000", [], {}, 1_000_001])
def test_invalid_user_token_limit_is_rejected_before_mutation(
    store: ModelProviderSettingsStore,
    roots: tuple[Path, Path],
    backend: FakeCredentialBackend,
    field: str,
    invalid: object,
) -> None:
    with pytest.raises(ModelProviderSettingsError) as caught:
        store.upsert_metadata(profile(**{field: invalid}), expected_revision=None)
    assert caught.value.code == f"{field}_invalid"
    assert not (roots[1] / SETTINGS_FILE_NAME).exists()
    assert not backend.read_calls
    assert not backend.write_calls
    assert not backend.delete_calls


@pytest.mark.parametrize("field", ["max_input_tokens", "max_output_tokens"])
def test_token_limit_change_and_clear_rotate_revision_without_touching_key(
    store: ModelProviderSettingsStore, backend: FakeCredentialBackend, field: str,
) -> None:
    created = create_profile(store)
    changed = store.upsert_metadata(
        profile(**{field: 65536}), expected_revision=str(created["revision"])
    )
    cleared = store.upsert_metadata(
        profile(**{field: None}), expected_revision=str(changed["revision"])
    )
    assert len({created["revision"], changed["revision"], cleared["revision"]}) == 3
    assert changed[field] == 65536
    assert cleared[field] is None
    with pytest.raises(ModelProviderSettingsError) as caught:
        store.invocation_policy("teacher-default", expected_revision=str(changed["revision"]))
    assert caught.value.code == "revision_conflict"
    assert not backend.read_calls
    assert not backend.write_calls
    assert not backend.delete_calls


@pytest.mark.parametrize("limits", [
    {}, {"max_input_tokens": 64000, "max_output_tokens": 65536},
])
@pytest.mark.parametrize("method", ["borrow_probe_context", "borrow_invocation_context"])
def test_borrowed_context_keeps_user_token_limits_or_none(
    store: ModelProviderSettingsStore,
    backend: FakeCredentialBackend,
    limits: dict[str, int],
    method: str,
) -> None:
    created = store.upsert_metadata(profile(**limits), expected_revision=None)
    configured = store.put_credential(
        "teacher-default", SECRET_ONE, expected_revision=str(created["revision"])
    )
    backend.read_calls.clear()
    with getattr(store, method)(
        "teacher-default", expected_revision=str(configured["revision"])
    ) as context:
        assert context.max_input_tokens == limits.get("max_input_tokens")
        assert context.max_output_tokens == limits.get("max_output_tokens")
        assert SECRET_ONE not in repr(context)
    assert len(backend.read_calls) == 1


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("present_limit", [None, "max_input_tokens", "max_output_tokens"])
def test_old_metadata_load_keeps_bytes_revision_and_does_not_borrow(
    store: ModelProviderSettingsStore,
    roots: tuple[Path, Path],
    backend: FakeCredentialBackend,
    monkeypatch: pytest.MonkeyPatch,
    legacy: bool,
    present_limit: str | None,
) -> None:
    created = create_profile(store)
    settings_path = roots[1] / SETTINGS_FILE_NAME
    raw = json.loads(settings_path.read_text(encoding="utf-8"))
    stored = raw["profiles"][0]
    stored.pop("max_input_tokens")
    stored.pop("max_output_tokens")
    if present_limit:
        stored[present_limit] = 65536
    if legacy:
        for field in ("provider_kind", "display_name", "base_url", "api_style",
                      "local_endpoint_policy", "endpoint_scope"):
            stored.pop(field)
    settings_path.write_text(json.dumps(raw), encoding="utf-8")
    before = settings_path.read_bytes()
    reloaded = ModelProviderSettingsStore(
        roots[1], project_root=roots[0], credential_backend=backend
    )
    def reject_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Read-only load cannot migrate metadata")
    monkeypatch.setattr(ModelProviderSettingsStore, "_save_unlocked", reject_write)
    listed = reloaded.list_metadata()[0]
    for field in ("max_input_tokens", "max_output_tokens"):
        assert listed[field] == (65536 if field == present_limit else None)
    assert listed["revision"] == created["revision"]
    assert settings_path.read_bytes() == before
    assert not backend.read_calls
    assert not backend.write_calls
    assert not backend.delete_calls


@pytest.mark.parametrize("field", ["max_input_tokens", "max_output_tokens"])
@pytest.mark.parametrize("invalid", [True, 0, -1, 8.5, "65536", {}, 1_000_001])
def test_invalid_stored_token_limit_fails_closed_without_mutation(
    store: ModelProviderSettingsStore,
    roots: tuple[Path, Path],
    backend: FakeCredentialBackend,
    field: str,
    invalid: object,
) -> None:
    create_profile(store)
    settings_path = roots[1] / SETTINGS_FILE_NAME
    raw = json.loads(settings_path.read_text(encoding="utf-8"))
    raw["profiles"][0][field] = invalid
    settings_path.write_text(json.dumps(raw), encoding="utf-8")
    before = settings_path.read_bytes()
    with pytest.raises(ModelProviderSettingsError) as caught:
        store.list_metadata()
    assert caught.value.code == "settings_corrupt"
    assert settings_path.read_bytes() == before
    assert not backend.read_calls


def test_metadata_and_credential_are_strictly_separated_and_responses_are_redacted(
    store: ModelProviderSettingsStore,
    roots: tuple[Path, Path],
    backend: FakeCredentialBackend,
) -> None:
    created = create_profile(store)
    assert created["credential_state"] == "not_configured"
    assert created["model_configured"] is False
    assert created["offline_workbench_available"] is True
    assert created["credential_ref"] == (CREDENTIAL_TARGET_PREFIX + "teacher-default")

    configured = store.put_credential(
        "teacher-default", SECRET_ONE, expected_revision=str(created["revision"])
    )
    assert configured["credential_state"] == "configured"
    assert configured["model_configured"] is True
    assert SECRET_ONE not in repr(store)
    assert SECRET_ONE not in repr(configured)

    for local_file in roots[1].iterdir():
        if local_file.is_file():
            local_bytes = local_file.read_bytes()
            assert SECRET_ONE.encode("utf-8") not in local_bytes
            assert b"api_key" not in local_bytes.lower()
    assert backend.values[configured["credential_ref"]] == SECRET_ONE

    listed = store.list_metadata()
    assert len(listed) == 1
    assert listed[0]["credential_state"] == "configured"
    assert SECRET_ONE not in json.dumps(listed, ensure_ascii=False)


def test_rotation_delete_and_synthetic_preflight_never_invoke_a_model(
    store: ModelProviderSettingsStore,
    backend: FakeCredentialBackend,
) -> None:
    created = create_profile(store)
    first = store.put_credential(
        "teacher-default", SECRET_ONE, expected_revision=str(created["revision"])
    )
    second = store.put_credential(
        "teacher-default", SECRET_TWO, expected_revision=str(first["revision"])
    )
    assert second["revision"] != first["revision"]
    assert backend.values[second["credential_ref"]] == SECRET_TWO

    readiness = store.synthetic_test_status("teacher-default")
    assert readiness["status"] == "ready_for_synthetic_test"
    assert readiness["ready"] is True
    assert readiness["model_invoked"] is False
    assert readiness["follow_redirects"] is False
    assert SECRET_TWO not in json.dumps(readiness, ensure_ascii=False)

    deleted = store.delete_credential(
        "teacher-default", expected_revision=str(second["revision"])
    )
    assert deleted["credential_state"] == "not_configured"
    assert deleted["revision"] != second["revision"]
    assert backend.values == {}
    assert store.synthetic_test_status("teacher-default") == {
        "profile_id": "teacher-default",
        "provider_id": "openai",
        "model_id": "gpt-5-mini",
        "base_url_policy": "openai_official_https_v1",
        "capabilities": ["text", "vision", "structured_output"],
        "status": "model_not_configured",
        "credential_state": "not_configured",
        "ready": False,
        "model_invoked": False,
        "follow_redirects": False,
        "offline_workbench_available": True,
    }


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"profile_id": "../escape"}, "profile_invalid"),
        ({"provider_id": "unlisted"}, "provider_not_allowed"),
        ({"model_id": "https://evil.invalid/?token=x"}, "model_not_allowed"),
        (
            {"base_url_policy": "https://evil.invalid/?api_key=x"},
            "base_url_policy_not_allowed",
        ),
        (
            {"credential_ref": "OtherApp/credential/teacher-default"},
            "credential_reference_invalid",
        ),
        ({"capabilities": ["text"]}, "capabilities_invalid"),
        (
            {"allowed_data_classes": ["raw_student_image"]},
            "data_policy_invalid",
        ),
        (
            {
                "allowed_data_classes": ["synthetic_only"],
                "image_egress": "redacted_question_only",
            },
            "data_policy_invalid",
        ),
        (
            {
                "allowed_data_classes": [
                    "synthetic_only",
                    "question_image_redacted",
                ],
                "image_egress": "deny",
            },
            "data_policy_invalid",
        ),
    ],
)
def test_profile_policy_is_closed_and_rejects_url_or_data_policy_injection(
    store: ModelProviderSettingsStore,
    overrides: dict[str, object],
    code: str,
) -> None:
    with pytest.raises(ModelProviderSettingsError) as caught:
        store.upsert_metadata(profile(**overrides), expected_revision=None)
    assert caught.value.code == code
    assert "evil.invalid" not in str(caught.value)
    assert "token" not in repr(caught.value)


def test_student_answer_image_policy_requires_explicit_vision_capability(
    store: ModelProviderSettingsStore,
) -> None:
    created = store.upsert_metadata(
        profile(
            allowed_data_classes=["synthetic_only", "student_answer_image"],
            image_egress="teacher_confirmed_student_pages",
        ),
        expected_revision=None,
    )
    assert "student_answer_image" in created["allowed_data_classes"]
    assert created["image_egress"] == "teacher_confirmed_student_pages"
    assert "vision" in created["capability_evidence"]["catalog"]

    with pytest.raises(ModelProviderSettingsError) as caught:
        store.upsert_metadata(
            profile(
                profile_id="custom-no-vision",
                provider_kind="openai_compatible",
                provider_id="openai_compatible",
                display_name="Custom no vision",
                model_id="manual-model",
                base_url_policy="openai_compatible_public_https_v1",
                base_url="https://models.example.invalid/v1",
                api_style="responses",
                local_endpoint_policy="deny",
                capabilities=["text", "structured_output"],
                allowed_data_classes=["synthetic_only", "student_answer_image"],
                image_egress="teacher_confirmed_student_pages",
            ),
            expected_revision=None,
        )
    assert caught.value.code == "data_policy_invalid"


@pytest.mark.parametrize(
    "policy",
    [
        ProviderPolicy(
            "bad",
            "bad_policy",
            "http://api.example.test/v1",
            (ModelPolicy("m", ("text",)),),
        ),
        ProviderPolicy(
            "bad",
            "bad_policy",
            "https://user:pass@api.example.test/v1",
            (ModelPolicy("m", ("text",)),),
        ),
        ProviderPolicy(
            "bad",
            "bad_policy",
            "https://api.example.test/v1?token=x",
            (ModelPolicy("m", ("text",)),),
        ),
        ProviderPolicy(
            "bad",
            "bad_policy",
            "https://api.example.test/v1#redirect",
            (ModelPolicy("m", ("text",)),),
        ),
        ProviderPolicy(
            "bad",
            "bad_policy",
            "https://api.example.test/v1%2fredirect",
            (ModelPolicy("m", ("text",)),),
        ),
        ProviderPolicy(
            "bad",
            "bad_policy",
            "https://api.example.test\\@evil.invalid/v1",
            (ModelPolicy("m", ("text",)),),
        ),
        ProviderPolicy(
            "bad",
            "bad_policy",
            "https://api.example.test/v1",
            (ModelPolicy("m", ("text",)),),
            True,
        ),
    ],
)
def test_injected_provider_catalog_cannot_enable_unsafe_url_semantics(
    roots: tuple[Path, Path],
    backend: FakeCredentialBackend,
    policy: ProviderPolicy,
) -> None:
    with pytest.raises(ModelProviderSettingsError) as caught:
        ModelProviderSettingsStore(
            roots[1],
            project_root=roots[0],
            credential_backend=backend,
            provider_policies={"bad": policy},
        )
    assert caught.value.code == "provider_catalog_invalid"


def test_invocation_policy_resolves_only_the_pinned_https_endpoint(
    store: ModelProviderSettingsStore,
) -> None:
    created = create_profile(store)
    descriptor = store.invocation_policy(
        "teacher-default", expected_revision=str(created["revision"])
    )
    parsed = urlsplit(str(descriptor["base_url"]))
    assert parsed.scheme == "https"
    assert parsed.hostname == "api.openai.com"
    assert parsed.username is None
    assert parsed.password is None
    assert parsed.query == ""
    assert parsed.fragment == ""
    assert descriptor["follow_redirects"] is False
    assert "credential" not in descriptor


def test_unavailable_credential_store_is_model_not_configured_but_offline_stays_up(
    roots: tuple[Path, Path],
) -> None:
    backend = FakeCredentialBackend(available=False)
    store = ModelProviderSettingsStore(
        roots[1], project_root=roots[0], credential_backend=backend
    )
    created = create_profile(store)
    assert created["credential_state"] == "credential_store_unavailable"
    assert created["model_configured"] is False
    assert created["offline_workbench_available"] is True
    status_value = store.synthetic_test_status("teacher-default")
    assert status_value["status"] == "model_not_configured"
    assert status_value["credential_state"] == "credential_store_unavailable"
    assert status_value["offline_workbench_available"] is True
    with pytest.raises(CredentialBackendUnavailable):
        store.put_credential(
            "teacher-default", SECRET_ONE, expected_revision=str(created["revision"])
        )


def test_missing_profile_preflight_is_nonfatal_and_offline_available(
    store: ModelProviderSettingsStore,
) -> None:
    assert store.synthetic_test_status("missing") == {
        "profile_id": "missing",
        "status": "model_not_configured",
        "ready": False,
        "model_invoked": False,
        "offline_workbench_available": True,
    }


def test_backend_exception_cannot_echo_the_submitted_secret(
    store: ModelProviderSettingsStore,
    backend: FakeCredentialBackend,
) -> None:
    created = create_profile(store)
    backend.fail_write_with_secret = True
    rendered_traceback = ""
    with pytest.raises(ModelProviderSettingsError) as caught:
        try:
            store.put_credential(
                "teacher-default",
                SECRET_ONE,
                expected_revision=str(created["revision"]),
            )
        except ModelProviderSettingsError:
            rendered_traceback = traceback.format_exc()
            raise
    assert caught.value.code == "credential_operation_failed"
    assert SECRET_ONE not in str(caught.value)
    assert SECRET_ONE not in repr(caught.value)
    assert SECRET_ONE not in rendered_traceback
    assert caught.value.__cause__ is None


def test_failed_metadata_commit_restores_the_previous_credential(
    store: ModelProviderSettingsStore,
    backend: FakeCredentialBackend,
    roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = create_profile(store)
    configured = store.put_credential(
        "teacher-default", SECRET_ONE, expected_revision=str(created["revision"])
    )
    persisted_before = (roots[1] / SETTINGS_FILE_NAME).read_bytes()

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("simulated local write failure")

    monkeypatch.setattr(settings_module.os, "replace", fail_replace)
    with pytest.raises(ModelProviderSettingsError) as caught:
        store.put_credential(
            "teacher-default",
            SECRET_TWO,
            expected_revision=str(configured["revision"]),
        )
    assert caught.value.code == "settings_write_failed"
    assert backend.values[configured["credential_ref"]] == SECRET_ONE
    assert (roots[1] / SETTINGS_FILE_NAME).read_bytes() == persisted_before
    assert SECRET_TWO not in str(caught.value)
    assert SECRET_TWO not in "".join(traceback.format_exception_only(caught.value))


def test_failed_metadata_commit_never_restores_a_deleted_credential(
    store: ModelProviderSettingsStore,
    backend: FakeCredentialBackend,
    roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = create_profile(store)
    configured = store.put_credential(
        "teacher-default", SECRET_ONE, expected_revision=str(created["revision"])
    )
    persisted_before = (roots[1] / SETTINGS_FILE_NAME).read_bytes()

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("simulated local write failure")

    monkeypatch.setattr(settings_module.os, "replace", fail_replace)
    with pytest.raises(ModelProviderSettingsError) as caught:
        store.delete_credential(
            "teacher-default", expected_revision=str(configured["revision"])
        )
    assert caught.value.code == "credential_deleted_metadata_stale"
    assert backend.values == {}
    assert (roots[1] / SETTINGS_FILE_NAME).read_bytes() == persisted_before
    assert store.synthetic_test_status("teacher-default")["ready"] is False


def test_failed_first_credential_commit_removes_the_unbound_credential(
    store: ModelProviderSettingsStore,
    backend: FakeCredentialBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = create_profile(store)

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("simulated local write failure")

    monkeypatch.setattr(settings_module.os, "replace", fail_replace)
    with pytest.raises(ModelProviderSettingsError) as caught:
        store.put_credential(
            "teacher-default", SECRET_ONE, expected_revision=str(created["revision"])
        )
    assert caught.value.code == "settings_write_failed"
    assert backend.values == {}


def test_credential_validation_errors_do_not_echo_input(
    store: ModelProviderSettingsStore,
) -> None:
    created = create_profile(store)
    secret = " bad-secret-with-leading-space"
    with pytest.raises(ModelProviderSettingsError) as caught:
        store.put_credential(
            "teacher-default", secret, expected_revision=str(created["revision"])
        )
    assert caught.value.code == "credential_invalid"
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)

    unicode_secret = "sk-valid-length-but-unicode-密钥"
    with pytest.raises(ModelProviderSettingsError) as unicode_caught:
        store.put_credential(
            "teacher-default",
            unicode_secret,
            expected_revision=str(created["revision"]),
        )
    assert unicode_caught.value.code == "credential_invalid"
    assert unicode_secret not in str(unicode_caught.value)


def test_probe_timestamp_is_canonical_and_cannot_be_used_as_a_secret_sink(
    store: ModelProviderSettingsStore,
) -> None:
    with pytest.raises(ModelProviderSettingsError) as caught:
        store.upsert_metadata(
            profile(
                last_probe={
                    "status": "ready_not_invoked",
                    "checked_at": "sk-not-a-timestampZ",
                    "error_code": None,
                    "model_invoked": False,
                }
            ),
            expected_revision=None,
        )
    assert caught.value.code == "probe_metadata_invalid"

    created = store.upsert_metadata(
        profile(
            last_probe={
                "status": "ready_not_invoked",
                "checked_at": "2026-08-26T18:30:59.123456Z",
                "error_code": None,
                "model_invoked": False,
            }
        ),
        expected_revision=None,
    )
    assert created["last_probe"]["checked_at"] == "2026-08-26T18:30:59.123456Z"


def test_stale_revision_is_rejected_across_store_instances_and_cannot_aba(
    roots: tuple[Path, Path],
    backend: FakeCredentialBackend,
) -> None:
    first_store = ModelProviderSettingsStore(
        roots[1], project_root=roots[0], credential_backend=backend
    )
    second_store = ModelProviderSettingsStore(
        roots[1], project_root=roots[0], credential_backend=backend
    )
    created = create_profile(first_store)
    updated = second_store.upsert_metadata(
        profile(model_id="gpt-5"), expected_revision=str(created["revision"])
    )
    assert updated["revision"] != created["revision"]
    with pytest.raises(ModelProviderSettingsError) as caught:
        first_store.upsert_metadata(
            profile(model_id="gpt-4.1"),
            expected_revision=str(created["revision"]),
        )
    assert caught.value.code == "revision_conflict"

    configured = first_store.put_credential(
        "teacher-default", SECRET_ONE, expected_revision=str(updated["revision"])
    )
    deleted = second_store.delete_credential(
        "teacher-default", expected_revision=str(configured["revision"])
    )
    assert deleted["revision"] not in {
        created["revision"],
        updated["revision"],
        configured["revision"],
    }
    with pytest.raises(ModelProviderSettingsError) as stale_rotation:
        first_store.put_credential(
            "teacher-default",
            SECRET_TWO,
            expected_revision=str(configured["revision"]),
        )
    assert stale_rotation.value.code == "revision_conflict"


def test_two_concurrent_updates_with_same_revision_have_exactly_one_winner(
    roots: tuple[Path, Path],
    backend: FakeCredentialBackend,
) -> None:
    seed_store = ModelProviderSettingsStore(
        roots[1], project_root=roots[0], credential_backend=backend
    )
    created = create_profile(seed_store)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    outcome_lock = threading.Lock()

    def update(model_id: str) -> None:
        local_store = ModelProviderSettingsStore(
            roots[1], project_root=roots[0], credential_backend=backend
        )
        barrier.wait()
        try:
            local_store.upsert_metadata(
                profile(model_id=model_id),
                expected_revision=str(created["revision"]),
            )
        except ModelProviderSettingsError as exc:
            result = exc.code
        else:
            result = "success"
        with outcome_lock:
            outcomes.append(result)

    threads = [
        threading.Thread(target=update, args=("gpt-5",)),
        threading.Thread(target=update, args=("gpt-4.1",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["revision_conflict", "success"]


def test_project_internal_metadata_root_is_rejected(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    with pytest.raises(ModelProviderSettingsError) as caught:
        ModelProviderSettingsStore(
            project_root / "runtime" / "credentials",
            project_root=project_root,
            credential_backend=FakeCredentialBackend(),
        )
    assert caught.value.code == "settings_root_not_external"


def test_hardlinked_metadata_file_is_rejected(
    store: ModelProviderSettingsStore,
    roots: tuple[Path, Path],
) -> None:
    create_profile(store)
    settings_path = roots[1] / SETTINGS_FILE_NAME
    second_link = roots[1] / "unexpected-hardlink.json"
    os.link(settings_path, second_link)
    try:
        with pytest.raises(ModelProviderSettingsError) as caught:
            store.list_metadata()
        assert caught.value.code == "unsafe_settings_path"
    finally:
        second_link.unlink()


def test_hardlinked_lock_file_is_rejected(
    store: ModelProviderSettingsStore,
    roots: tuple[Path, Path],
) -> None:
    store.list_metadata()
    lock_path = roots[1] / LOCK_FILE_NAME
    second_link = roots[1] / "unexpected-lock-link"
    os.link(lock_path, second_link)
    try:
        with pytest.raises(ModelProviderSettingsError) as caught:
            store.list_metadata()
        assert caught.value.code == "unsafe_settings_path"
    finally:
        second_link.unlink()


def test_symlink_or_reparse_metadata_root_is_rejected(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    real_external = tmp_path / "real-external"
    linked_external = tmp_path / "linked-external"
    project_root.mkdir()
    real_external.mkdir()
    try:
        linked_external.symlink_to(real_external, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this Windows account cannot create a test symlink")
    with pytest.raises(ModelProviderSettingsError) as caught:
        ModelProviderSettingsStore(
            linked_external,
            project_root=project_root,
            credential_backend=FakeCredentialBackend(),
        )
    assert caught.value.code == "unsafe_settings_path"


def test_metadata_permissions_and_atomic_file_shape(
    store: ModelProviderSettingsStore,
    roots: tuple[Path, Path],
) -> None:
    create_profile(store)
    root_stat = roots[1].lstat()
    file_stat = (roots[1] / SETTINGS_FILE_NAME).lstat()
    assert not stat.S_ISLNK(root_stat.st_mode)
    assert stat.S_ISREG(file_stat.st_mode)
    assert file_stat.st_nlink == 1
    if os.name != "nt":
        assert stat.S_IMODE(root_stat.st_mode) == 0o700
        assert stat.S_IMODE(file_stat.st_mode) == 0o600
    assert not list(roots[1].glob(f".{SETTINGS_FILE_NAME}.*"))


def test_corrupt_or_secret_bearing_metadata_fails_closed_without_echo(
    store: ModelProviderSettingsStore,
    roots: tuple[Path, Path],
) -> None:
    create_profile(store)
    settings_path = roots[1] / SETTINGS_FILE_NAME
    raw = json.loads(settings_path.read_text(encoding="utf-8"))
    raw["profiles"][0]["api_key"] = SECRET_ONE
    settings_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ModelProviderSettingsError) as caught:
        store.list_metadata()
    assert caught.value.code == "settings_corrupt"
    assert SECRET_ONE not in str(caught.value)
    assert SECRET_ONE not in repr(caught.value)


def test_legacy_v1_profile_shape_is_loaded_and_migrated_on_next_write(
    store: ModelProviderSettingsStore,
    roots: tuple[Path, Path],
    backend: FakeCredentialBackend,
) -> None:
    created = create_profile(store)
    settings_path = roots[1] / SETTINGS_FILE_NAME
    raw = json.loads(settings_path.read_text(encoding="utf-8"))
    legacy_fields = {
        "profile_id",
        "provider_id",
        "model_id",
        "base_url_policy",
        "credential_ref",
        "revision",
        "capabilities",
        "allowed_data_classes",
        "image_egress",
        "last_probe",
    }
    raw["profiles"][0] = {
        key: value
        for key, value in raw["profiles"][0].items()
        if key in legacy_fields
    }
    settings_path.write_text(
        json.dumps(raw, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )

    reloaded = ModelProviderSettingsStore(
        roots[1],
        project_root=roots[0],
        credential_backend=backend,
    )
    listed = reloaded.list_metadata()[0]
    assert listed["provider_kind"] == "preset"
    assert listed["display_name"] == "OpenAI"
    assert listed["base_url"] == "https://api.openai.com/v1"
    assert listed["api_style"] == "responses"
    assert listed["revision"] == created["revision"]

    updated = reloaded.upsert_metadata(
        profile(), expected_revision=str(listed["revision"])
    )
    migrated = json.loads(settings_path.read_text(encoding="utf-8"))["profiles"][0]
    assert updated["revision"] != created["revision"]
    assert {
        "provider_kind",
        "display_name",
        "base_url",
        "api_style",
        "local_endpoint_policy",
        "endpoint_scope",
    }.issubset(migrated)


def test_provider_change_requires_prior_key_deletion(
    roots: tuple[Path, Path],
    backend: FakeCredentialBackend,
) -> None:
    store = ModelProviderSettingsStore(
        roots[1], project_root=roots[0], credential_backend=backend
    )
    created = create_profile(store)
    configured = store.put_credential(
        "teacher-default", SECRET_ONE, expected_revision=str(created["revision"])
    )
    with pytest.raises(ModelProviderSettingsError) as caught:
        store.upsert_metadata(
            profile(
                provider_id="deepseek",
                model_id="deepseek-v4-flash",
                base_url_policy="deepseek_official_https_v1",
                capabilities=["text", "structured_output"],
            ),
            expected_revision=str(configured["revision"]),
        )
    assert caught.value.code == "credential_rotation_required"
    deleted = store.delete_credential(
        "teacher-default", expected_revision=str(configured["revision"])
    )
    changed = store.upsert_metadata(
        profile(
            provider_id="deepseek",
            model_id="deepseek-v4-flash",
            base_url_policy="deepseek_official_https_v1",
            capabilities=["text", "structured_output"],
        ),
        expected_revision=str(deleted["revision"]),
    )
    assert changed["provider_id"] == "deepseek"
    assert changed["credential_state"] == "not_configured"


def test_windows_backend_contract_is_current_user_generic_and_non_roaming() -> None:
    backend = WindowsCredentialManagerBackend()
    rendered = repr(backend)
    assert "current_user=True" in rendered
    assert "roaming=False" in rendered
    assert backend._CRED_TYPE_GENERIC == 1
    assert backend._CRED_PERSIST_LOCAL_MACHINE == 2
    assert "secret" not in rendered.lower()


def test_default_deepseek_policy_uses_current_v4_names_and_official_root() -> None:
    policy = DEFAULT_PROVIDER_POLICIES["deepseek"]
    assert policy.base_url == "https://api.deepseek.com"
    assert [model.model_id for model in policy.models] == [
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    ]
    assert "deepseek-chat" not in repr(policy)
    assert "deepseek-reasoner" not in repr(policy)


def test_no_test_uses_the_real_windows_credential_manager(
    monkeypatch: pytest.MonkeyPatch,
    roots: tuple[Path, Path],
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("real Windows Credential Manager must not be used")

    monkeypatch.setattr(WindowsCredentialManagerBackend, "write", forbidden)
    monkeypatch.setattr(WindowsCredentialManagerBackend, "read", forbidden)
    monkeypatch.setattr(WindowsCredentialManagerBackend, "delete", forbidden)
    fake = FakeCredentialBackend()
    store = ModelProviderSettingsStore(
        roots[1], project_root=roots[0], credential_backend=fake
    )
    created = create_profile(store)
    store.put_credential(
        "teacher-default", SECRET_ONE, expected_revision=str(created["revision"])
    )
    assert fake.values
