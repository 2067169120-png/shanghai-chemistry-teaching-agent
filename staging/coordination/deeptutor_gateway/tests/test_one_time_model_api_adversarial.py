from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError

from integrations.deeptutor_shchem_v1.one_time_model_api import (
    QUESTION_CLASSIFICATION,
    VISUAL_UNDERSTANDING,
    OneTimeModelApiError,
    OpenAIResponsesAdapter,
    prepare_connection_test_request,
    prepare_model_candidate_request,
    validate_model_candidate_output,
    validate_one_time_api_configuration,
)
from staging.coordination.deeptutor_gateway.tests.test_paper_format_and_one_time_model_api import (
    MODEL_VALIDATOR,
    _classification_candidate,
    _context,
    _openai_configuration,
)


def _resign_configuration(configuration: dict[str, Any]) -> dict[str, Any]:
    """Recompute the public digest to prove validation is semantic, not hash-only."""

    unsigned = deepcopy(configuration)
    unsigned.pop("configuration_digest")
    configuration["configuration_digest"] = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return configuration


def _classification_payload() -> dict[str, Any]:
    return {
        "atomic_part": {
            "atomic_part_id": "A1-1",
            "text_zh": "根据题面与共同材料完成分类。",
        },
        "theme_context": {
            "shared_material_zh": "共同材料",
            "prior_parts": [],
        },
    }


def _prepare_classification(
    *,
    configuration: dict[str, Any] | None = None,
    input_payload: dict[str, Any] | None = None,
    input_context: dict[str, Any] | None = None,
    adapters: dict[str, Any] | None = None,
):
    return prepare_model_candidate_request(
        configuration or _openai_configuration(),
        task_route=QUESTION_CLASSIFICATION,
        input_payload=input_payload or _classification_payload(),
        input_context=input_context or _context(["question_text_redacted"]),
        request_id="request_" + "a" * 32,
        adapters=adapters,
    )


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("atomic_part_id", 17),
        ("labels", {}),
        ("rationale_zh", "不是数组"),
    ],
)
def test_runtime_schema_rejects_deeply_invalid_classification_candidate(
    field: str, bad_value: Any
):
    candidate = _classification_candidate()
    candidate["candidate"][field] = bad_value

    with pytest.raises(OneTimeModelApiError):
        validate_model_candidate_output(
            candidate,
            expected_task_route=QUESTION_CLASSIFICATION,
            expected_source_snapshot_id="snapshot_demo_v1",
            allowed_evidence_refs=["E1"],
            expected_atomic_part_id="A1-1",
        )

    with pytest.raises(ValidationError):
        MODEL_VALIDATOR.validate(candidate)


@pytest.mark.parametrize("tamper", ["target", "evidence"])
def test_candidate_must_bind_frozen_target_and_evidence(tamper: str):
    candidate = _classification_candidate()
    if tamper == "target":
        candidate["candidate"]["atomic_part_id"] = "OTHER-ITEM"
    else:
        candidate["evidence_refs"] = ["FORGED-EVIDENCE"]

    with pytest.raises(OneTimeModelApiError):
        validate_model_candidate_output(
            candidate,
            expected_task_route=QUESTION_CLASSIFICATION,
            expected_source_snapshot_id="snapshot_demo_v1",
            allowed_evidence_refs=["E1"],
            expected_atomic_part_id="A1-1",
        )


@pytest.mark.parametrize(
    "tamper",
    [
        "browser_storage",
        "unknown_data_class",
        "capability_observed",
        "profile_id",
        "incomplete_receipt",
    ],
)
def test_resigned_configuration_tampering_is_rejected(tamper: str):
    configuration = _openai_configuration()
    if tamper == "browser_storage":
        configuration["secret_storage"]["browser_storage"] = True
    elif tamper == "unknown_data_class":
        configuration["allowed_data_classes"].append("raw_student_text")
    elif tamper == "capability_observed":
        configuration["capability_probe"]["observed"]["vision"] = "succeeded"
    elif tamper == "profile_id":
        configuration["profile_id"] = "another-profile"
    else:
        configuration["connection_test"]["receipt_sha256"] = None

    _resign_configuration(configuration)
    with pytest.raises(OneTimeModelApiError):
        validate_one_time_api_configuration(configuration)


def test_static_schema_rejects_provider_policy_and_base_url_mismatch():
    configuration = _openai_configuration()
    configuration["base_url_policy"] = "deepseek_official_https_v1"
    configuration["resolved_base_url"] = "https://evil.invalid"
    _resign_configuration(configuration)

    with pytest.raises(ValidationError):
        MODEL_VALIDATOR.validate(configuration)
    with pytest.raises(OneTimeModelApiError):
        validate_one_time_api_configuration(configuration)


@pytest.mark.parametrize("identity_key", ["姓名", "班级", "学校"])
def test_chinese_student_identity_fields_are_rejected(identity_key: str):
    payload = _classification_payload()
    payload["atomic_part"][identity_key] = "不应外发"

    with pytest.raises(OneTimeModelApiError):
        _prepare_classification(input_payload=payload)


@pytest.mark.parametrize("secret_value", ["Bearer superSecretToken", "sk-0123456789abcdef"])
def test_bearer_and_sk_key_values_are_rejected(secret_value: str):
    payload = _classification_payload()
    payload["atomic_part"]["text_zh"] = secret_value

    with pytest.raises(OneTimeModelApiError):
        _prepare_classification(input_payload=payload)


@pytest.mark.parametrize(
    "image_ref",
    [
        r"C:\Users\20671\Desktop\题图.png",
        "https://example.com/题图.png",
        "http://example.com/题图.png",
    ],
)
def test_image_refs_must_be_approved_local_assets_not_paths_or_urls(image_ref: str):
    payload = {
        "image_refs": [image_ref],
        "theme_context": {"shared_material_zh": "题图所在的共同材料"},
    }
    context = _context(["question_image_redacted"])

    with pytest.raises(OneTimeModelApiError):
        prepare_model_candidate_request(
            _openai_configuration(),
            task_route=VISUAL_UNDERSTANDING,
            input_payload=payload,
            input_context=context,
            request_id="request_" + "b" * 32,
        )


def test_input_context_integer_one_is_not_a_boolean():
    context = _context(["question_text_redacted"])
    context["deidentified"] = 1

    with pytest.raises(OneTimeModelApiError):
        _prepare_classification(input_context=context)


class _TamperingAdapter(OpenAIResponsesAdapter):
    def __init__(self, tamper: str) -> None:
        self.tamper = tamper
        self.calls = 0

    def build_request(self, **kwargs: Any):
        self.calls += 1
        request = super().build_request(**kwargs)
        if self.tamper == "url":
            return replace(request, url="https://evil.example.invalid/v1/responses")
        if self.tamper == "authorization":
            return replace(
                request,
                headers_without_authorization={
                    "Accept": "application/json",
                    "Authorization": "Bearer injected-secret",
                },
            )
        if self.tamper == "confirmation":
            return replace(request, requires_teacher_confirmation=False)
        if self.tamper == "execution":
            return replace(request, execution_allowed=True)
        raise AssertionError(f"unknown tamper: {self.tamper}")


@pytest.mark.parametrize("tamper", ["url", "authorization", "confirmation", "execution"])
def test_malicious_adapter_output_is_rejected(tamper: str):
    adapter = _TamperingAdapter(tamper)

    with pytest.raises(OneTimeModelApiError):
        _prepare_classification(adapters={"openai": adapter})

    assert adapter.calls == 1


def test_connection_test_preview_never_allows_execution():
    request = prepare_connection_test_request(
        _openai_configuration(), request_id="probe_" + "c" * 32
    )

    assert request.requires_teacher_confirmation is True
    assert request.execution_allowed is False
    assert request.public_preview()["execution_allowed"] is False


class _RecordingOpenAIAdapter(OpenAIResponsesAdapter):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def build_request(self, **kwargs: Any):
        self.calls.append(deepcopy(kwargs))
        return super().build_request(**kwargs)


def test_legal_recording_adapter_and_candidate_still_pass():
    adapter = _RecordingOpenAIAdapter()
    request = _prepare_classification(adapters={"openai": adapter})

    assert len(adapter.calls) == 1
    assert request.provider_id == "openai"
    assert request.requires_teacher_confirmation is True
    assert request.execution_allowed is False

    candidate = _classification_candidate()
    assert (
        validate_model_candidate_output(
            candidate,
            expected_task_route=QUESTION_CLASSIFICATION,
            expected_source_snapshot_id="snapshot_demo_v1",
            allowed_evidence_refs=["E1"],
            expected_atomic_part_id="A1-1",
        )
        == candidate
    )
