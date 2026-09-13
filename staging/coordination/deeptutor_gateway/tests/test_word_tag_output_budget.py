from __future__ import annotations

import json
import time
from contextlib import contextmanager

import pytest
from test_desktop_visual_import_facade import (
    _png,
)
from test_desktop_visual_import_facade import (
    desktop_paths as desktop_paths,  # noqa: PLC0414
)
from test_word_semantic_tags import (
    SemanticProvider,
    SemanticTransport,
    _choices,
    _make_facade,
)

from integrations.deeptutor_shchem_v1 import desktop_word_semantic_tags as semantic
from integrations.deeptutor_shchem_v1 import visual_provider_runtime as runtime
from integrations.deeptutor_shchem_v1.model_provider_probe import ProbeTransportResponse
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    ModelProviderProbeContext,
)

OFFICIAL_DEEPSEEK = "https://api.deepseek.com/v1"
ALLOWED_SUMMARY_KEYS = {
    "status",
    "incomplete_reason",
    "finish_reason",
    "usage",
    "reasoning_tokens",
}


class BudgetProvider(SemanticProvider):
    def __init__(
        self,
        *,
        model_id: str,
        base_url: str,
        api_style: str = "responses",
        vision: bool = True,
        max_input_tokens: int | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        super().__init__(vision=vision)
        self.model_id = model_id
        self.base_url = base_url
        self.api_style = api_style
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens
        self.context_budget_overrides = {}

    def list_metadata(self):
        rows = super().list_metadata()
        rows[0]["model_id"] = self.model_id
        rows[0]["api_style"] = self.api_style
        rows[0]["max_input_tokens"] = self.max_input_tokens
        rows[0]["max_output_tokens"] = self.max_output_tokens
        return rows

    @contextmanager
    def borrow_invocation_context(self, profile_id: str, *, expected_revision: str):
        assert profile_id == "semantic"
        assert expected_revision == self.revision
        self.borrow_calls += 1
        limits = {
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            **self.context_budget_overrides,
        }
        yield ModelProviderProbeContext(
            profile_id=profile_id,
            provider_id="openai_compatible",
            model_id=self.model_id,
            base_url_policy="openai_compatible_public_https_v1",
            base_url=self.base_url,
            revision=self.revision,
            api_key="test-only",
            provider_kind="openai_compatible",
            api_style=self.api_style,
            local_endpoint_policy="deny",
            **limits,
        )

    def invocation_policy(self, profile_id: str, *, expected_revision: str):
        policy = super().invocation_policy(
            profile_id, expected_revision=expected_revision
        )
        policy.update(
            {
                "base_url": self.base_url,
                "model_id": self.model_id,
                "max_input_tokens": self.max_input_tokens,
                "max_output_tokens": self.max_output_tokens,
            }
        )
        return policy


class DeadlineTransport(SemanticTransport):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.deadlines: list[float] = []

    def send(self, request, *, cancel_event, deadline_monotonic):
        self.deadlines.append(deadline_monotonic)
        return super().send(
            request,
            cancel_event=cancel_event,
            deadline_monotonic=deadline_monotonic,
        )


class TruncatingTransport(SemanticTransport):
    def send(self, request, *, cancel_event, deadline_monotonic):
        del cancel_event, deadline_monotonic
        body = json.loads(request.body)
        self.calls.append({"request": request, "body": body})
        payload = {
            "status": "incomplete",
            "error": None,
            "incomplete_details": {"reason": "max_output_tokens"},
            "output_text": "SECRET_REASONING_AND_PARTIAL_LABEL",
            "usage": {
                "input_tokens": 101,
                "output_tokens": 32000,
                "total_tokens": 32101,
                "output_tokens_details": {"reasoning_tokens": 31900},
                "private_diagnostic": "SECRET_PROVIDER_BODY",
            },
        }
        return ProbeTransportResponse(
            http_status=200,
            content_type="application/json",
            content_encoding=None,
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            latency_ms=1,
            model_invoked=True,
        )


@pytest.mark.parametrize(
    ("model_id", "base_url", "expected"),
    [
        (
            "deepseek-v4-flash",
            OFFICIAL_DEEPSEEK,
            {"max_output_tokens": 32000, "timeout_seconds": 300},
        ),
        (
            "deepseek-v4-pro",
            OFFICIAL_DEEPSEEK,
            {"max_output_tokens": 32000, "timeout_seconds": 300},
        ),
        (
            "deepseek-v4-flash-vision-exp",
            OFFICIAL_DEEPSEEK,
            {"max_output_tokens": 32000, "timeout_seconds": 300},
        ),
        (
            "deepseek-v4-pro",
            "https://proxy.api.deepseek.com/v1",
            {"max_output_tokens": 8192, "timeout_seconds": 180},
        ),
        (
            "deepseek-v4-pro",
            "http://api.deepseek.com/v1",
            {"max_output_tokens": 8192, "timeout_seconds": 180},
        ),
        (
            "deepseek-v3",
            OFFICIAL_DEEPSEEK,
            {"max_output_tokens": 8192, "timeout_seconds": 180},
        ),
    ],
)
def test_tag_request_policy_allows_only_exact_official_v4_models(
    desktop_paths, tmp_path, model_id, base_url, expected
):
    provider = BudgetProvider(model_id=model_id, base_url=base_url, vision=False)
    facade = _make_facade(
        desktop_paths,
        tmp_path,
        provider,
        SemanticTransport(),
        question_count=1,
    )

    plan = facade.word_semantic_tag_preview(
        _choices(facade, 1), "semantic", provider.revision
    )
    assert plan["request_policy"] == expected
    service_plan = facade._word_semantic_tags()._plans[plan["plan_id"]]
    assert service_plan["runtime_revision"] == semantic.REVISION
    assert service_plan["request_policy"] == expected


@pytest.mark.parametrize("image", [False, True])
def test_run_uses_disclosed_budget_for_text_and_image_requests(
    desktop_paths, tmp_path, image
):
    provider = BudgetProvider(
        model_id="deepseek-v4-flash-vision-exp" if image else "deepseek-v4-pro",
        base_url=OFFICIAL_DEEPSEEK,
        vision=True,
    )
    transport = DeadlineTransport(image_evidence=image)
    facade = _make_facade(
        desktop_paths,
        tmp_path,
        provider,
        transport,
        image=image,
        question_count=2 if image else 1,
    )
    choices = _choices(facade, 2 if image else 1)
    plan = facade.word_semantic_tag_preview(choices, "semantic", provider.revision)
    units = facade._word_semantic_tags()._plans[plan["plan_id"]]["units"]
    assert len(units) == (2 if image else 1)
    assert all(unit["status"] == "ready" for unit in units)

    result = facade.word_semantic_tag_run(
        plan["plan_id"], plan["revision"], confirmed=True
    )

    assert result["finished"] is True
    assert all(item["status"] == "ready" for item in result["items"])
    assert all(
        item["response_summary"]["status"] == "completed"
        and set(item["response_summary"]) <= ALLOWED_SUMMARY_KEYS
        for item in result["items"]
    )
    assert all(call["body"]["max_output_tokens"] == 32000 for call in transport.calls)
    if image:
        assert any(
            part.get("type") == "input_image"
            for part in transport.calls[1]["body"]["input"][0]["content"]
        )
    assert len(transport.deadlines) == len(transport.calls)
    assert all(
        294 <= deadline - time.monotonic() <= 301 for deadline in transport.deadlines
    )


def test_run_uses_small_budget_and_timeout_for_non_official_endpoint(
    desktop_paths, tmp_path
):
    provider = BudgetProvider(
        model_id="deepseek-v4-pro",
        base_url="https://proxy.api.deepseek.com/v1",
        vision=False,
    )
    transport = DeadlineTransport()
    facade = _make_facade(
        desktop_paths, tmp_path, provider, transport, question_count=1
    )
    plan = facade.word_semantic_tag_preview(
        _choices(facade, 1), "semantic", provider.revision
    )
    facade.word_semantic_tag_run(plan["plan_id"], plan["revision"], confirmed=True)

    assert transport.calls[0]["body"]["max_output_tokens"] == 8192
    assert 174 <= transport.deadlines[0] - time.monotonic() <= 181


@pytest.mark.parametrize("image", [False, True])
@pytest.mark.parametrize("output_budget", [128, 50000])
@pytest.mark.parametrize("input_budget", [None, 500000])
def test_user_budgets_match_preview_frozen_policy_and_actual_wire(
    desktop_paths, tmp_path, image, output_budget, input_budget
):
    provider = BudgetProvider(
        model_id="deepseek-v4-flash-vision-exp" if image else "deepseek-v4-pro",
        base_url=OFFICIAL_DEEPSEEK,
        vision=image,
        max_input_tokens=input_budget,
        max_output_tokens=output_budget,
    )
    transport = DeadlineTransport(image_evidence=image)
    facade = _make_facade(
        desktop_paths,
        tmp_path,
        provider,
        transport,
        image=image,
        question_count=2 if image else 1,
    )
    plan = facade.word_semantic_tag_preview(
        _choices(facade, 2 if image else 1), "semantic", provider.revision
    )
    expected = {"max_output_tokens": output_budget, "timeout_seconds": 300}
    if input_budget is not None:
        expected["max_input_tokens"] = input_budget
    assert plan["request_policy"] == expected
    frozen = facade._word_semantic_tags()._plans[plan["plan_id"]]
    assert frozen["request_policy"] == expected
    assert len(frozen["units"]) == (2 if image else 1)
    assert all(unit["status"] == "ready" for unit in frozen["units"])

    result = facade.word_semantic_tag_run(
        plan["plan_id"], plan["revision"], confirmed=True
    )

    assert result["finished"] is True
    assert all(item["status"] == "ready" for item in result["items"])
    assert len(transport.calls) == (2 if image else 1)
    assert all(
        call["body"]["max_output_tokens"] == output_budget for call in transport.calls
    )
    assert all("max_input_tokens" not in call["body"] for call in transport.calls)


@pytest.mark.parametrize("field", ["max_input_tokens", "max_output_tokens"])
@pytest.mark.parametrize("changed_at", ["policy", "borrowed_context"])
def test_budget_drift_is_blocked_before_transport(
    desktop_paths, tmp_path, field, changed_at
):
    provider = BudgetProvider(
        model_id="deepseek-v4-pro",
        base_url=OFFICIAL_DEEPSEEK,
        vision=False,
        max_input_tokens=500000,
        max_output_tokens=50000,
    )
    transport = DeadlineTransport()
    facade = _make_facade(
        desktop_paths, tmp_path, provider, transport, question_count=1
    )
    plan = facade.word_semantic_tag_preview(
        _choices(facade, 1), "semantic", provider.revision
    )
    replacement = 400000 if field == "max_input_tokens" else 40000
    if changed_at == "policy":
        setattr(provider, field, replacement)
        with pytest.raises(semantic.WordSemanticTagError, match="重新预览"):
            facade.word_semantic_tag_run(
                plan["plan_id"], plan["revision"], confirmed=True
            )
        assert provider.borrow_calls == 0
    else:
        provider.context_budget_overrides[field] = replacement
        result = facade.word_semantic_tag_run(
            plan["plan_id"], plan["revision"], confirmed=True
        )
        assert len(result["items"]) == 1
        assert result["items"][0]["status"] == "failed"
        assert "输入或输出预算已变" in result["items"][0]["note"]
    assert transport.calls == []


def test_disclosed_input_budget_can_reject_locally_without_transport(
    desktop_paths, tmp_path
):
    provider = BudgetProvider(
        model_id="deepseek-v4-pro",
        base_url=OFFICIAL_DEEPSEEK,
        vision=False,
        max_input_tokens=1,
        max_output_tokens=50000,
    )
    transport = DeadlineTransport()
    facade = _make_facade(
        desktop_paths, tmp_path, provider, transport, question_count=1
    )
    plan = facade.word_semantic_tag_preview(
        _choices(facade, 1), "semantic", provider.revision
    )
    assert plan["request_policy"]["max_input_tokens"] == 1
    result = facade.word_semantic_tag_run(
        plan["plan_id"], plan["revision"], confirmed=True
    )
    assert result["items"][0]["status"] == "failed"
    assert transport.calls == []


def test_word_image_policy_receives_dimensions_from_verified_source_bytes(
    desktop_paths, tmp_path, monkeypatch
):
    provider = BudgetProvider(
        model_id="deepseek-v4-flash-vision-exp",
        base_url=OFFICIAL_DEEPSEEK,
        max_output_tokens=50000,
    )
    transport = DeadlineTransport(image_evidence=True)
    facade = _make_facade(
        desktop_paths, tmp_path, provider, transport, image=True, question_count=2
    )
    captured = []
    original_policy = facade._preparation_image_policy

    def verified_policy(payload, *args):
        captured.extend(payload["image_assets"])
        return original_policy(payload, *args)

    monkeypatch.setattr(facade, "_preparation_image_policy", verified_policy)
    plan = facade.word_semantic_tag_preview(
        _choices(facade, 2), "semantic", provider.revision
    )
    image_unit = next(unit for unit in plan["units"] if unit["images"])
    sha = image_unit["images"][0]["sha256"]
    assert facade.word_semantic_tag_image(plan["plan_id"], sha) == _png("blue")
    assert captured and all(
        (row["width"], row["height"]) == (24, 32) for row in captured
    )
    assert all(row["sha256"] == sha for row in captured)
    assert all(unit["status"] == "ready" for unit in plan["units"])
    assert provider.borrow_calls == 0 and transport.calls == []


def test_changed_source_image_bytes_invalidate_frozen_plan_before_borrow_or_send(
    desktop_paths, tmp_path, monkeypatch
):
    provider = BudgetProvider(
        model_id="deepseek-v4-flash-vision-exp",
        base_url=OFFICIAL_DEEPSEEK,
        max_output_tokens=50000,
    )
    transport = DeadlineTransport(image_evidence=True)
    facade = _make_facade(
        desktop_paths, tmp_path, provider, transport, image=True, question_count=2
    )
    plan = facade.word_semantic_tag_preview(
        _choices(facade, 2), "semantic", provider.revision
    )
    assert all(unit["status"] == "ready" for unit in plan["units"])
    reader = facade._word_questions().reader
    original_read = reader.word_asset_bytes

    def changed_pixels(*args, **kwargs):
        return {**original_read(*args, **kwargs), "bytes": _png("red")}

    monkeypatch.setattr(reader, "word_asset_bytes", changed_pixels)
    with pytest.raises(semantic.WordSemanticTagError, match="重新预览"):
        facade.word_semantic_tag_run(plan["plan_id"], plan["revision"], confirmed=True)
    assert provider.borrow_calls == 0 and transport.calls == []


@pytest.mark.parametrize("field", ["max_input_tokens", "max_output_tokens"])
@pytest.mark.parametrize("value", [0, True, 1.5, "50000", 1000001])
def test_invalid_explicit_tag_budget_cannot_silently_use_recommendation(field, value):
    with pytest.raises(semantic.WordSemanticTagError) as error:
        semantic.tag_request_policy({field: value})
    assert error.value.code == field + "_invalid"
    assert "重新保存模型配置" in str(error.value)


def test_budget_is_bound_to_plan_and_changed_budget_stops_before_transport(
    desktop_paths, tmp_path
):
    provider = BudgetProvider(
        model_id="deepseek-v4-pro", base_url=OFFICIAL_DEEPSEEK, vision=False
    )
    transport = DeadlineTransport()
    facade = _make_facade(
        desktop_paths, tmp_path, provider, transport, question_count=1
    )
    choice = _choices(facade, 1)[0]
    plan = facade.word_semantic_tag_preview([choice], "semantic", provider.revision)
    facade._word_semantic_tags()._plans[plan["plan_id"]]["request_policy"] = {
        "max_output_tokens": 8192,
        "timeout_seconds": 180,
    }

    result = facade.word_semantic_tag_run(
        plan["plan_id"], plan["revision"], confirmed=True
    )

    assert transport.calls == []
    assert len(result["items"]) == 1
    assert result["items"][0]["status"] == "failed"
    assert "输出预算已变" in result["items"][0]["note"]


@pytest.mark.parametrize(
    "policy",
    [
        None,
        {},
        {"base_url": None, "model_id": "deepseek-v4-pro"},
        {"base_url": [], "model_id": {}},
        {
            "base_url": "https://api.deepseek.com/v1?leak=1",
            "model_id": "deepseek-v4-pro",
        },
        {
            "base_url": "https://api.deepseek.com.attacker/v1",
            "model_id": "deepseek-v4-pro",
        },
    ],
)
def test_budget_policy_rejects_malicious_or_non_scalar_provider_policy(policy):
    assert semantic.tag_request_policy(policy) == {
        "max_output_tokens": 8192,
        "timeout_seconds": 180,
    }


def test_responses_summary_is_closed_and_keeps_only_safe_budget_diagnostics():
    raw = json.dumps(
        {
            "status": "incomplete",
            "incomplete_details": {
                "reason": "max_output_tokens",
                "message": "SECRET_ERROR_TEXT",
            },
            "output_text": "SECRET_REASONING_TEXT",
            "reasoning_content": "SECRET_REASONING_CONTENT",
            "error": {"message": "SECRET_PROVIDER_ERROR"},
            "usage": {
                "input_tokens": 12,
                "output_tokens": 32000,
                "total_tokens": 32012,
                "output_tokens_details": {"reasoning_tokens": 31900},
                "private": "SECRET_USAGE_FIELD",
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")

    summary = runtime.structured_response_summary("responses", raw)

    assert set(summary) <= ALLOWED_SUMMARY_KEYS
    assert summary["status"] == "incomplete"
    assert summary["incomplete_reason"] == "max_output_tokens"
    assert summary["usage"] == {
        "input_tokens": 12,
        "output_tokens": 32000,
        "total_tokens": 32012,
    }
    assert summary["reasoning_tokens"] == 31900
    assert "SECRET" not in json.dumps(summary, ensure_ascii=False)


def test_chat_summary_keeps_finish_reason_and_reasoning_count_without_message_text():
    raw = json.dumps(
        {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": "SECRET_CHAT_TEXT", "refusal": None},
                }
            ],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 9,
                "total_tokens": 13,
                "completion_tokens_details": {"reasoning_tokens": 7},
            },
            "error": {"message": "SECRET_CHAT_ERROR"},
        },
        ensure_ascii=False,
    ).encode("utf-8")

    summary = runtime.structured_response_summary("chat_completions", raw)

    assert summary["finish_reason"] == "length"
    assert summary["usage"] == {
        "prompt_tokens": 4,
        "completion_tokens": 9,
        "total_tokens": 13,
    }
    assert summary["reasoning_tokens"] == 7
    assert set(summary) <= ALLOWED_SUMMARY_KEYS
    assert "SECRET" not in json.dumps(summary, ensure_ascii=False)


@pytest.mark.parametrize(
    "api_style, raw",
    [
        ("responses", b"not-json"),
        ("responses", b'{"status": "completed", "status": "incomplete"}'),
        ("chat_completions", b"[]"),
        ("unknown", b"{}"),
        ("responses", None),
        ("responses", bytearray(b"{}")),
    ],
)
def test_summary_malformed_or_wrong_types_is_safe_empty(api_style, raw):
    assert runtime.structured_response_summary(api_style, raw) == {}


def test_summary_oversized_body_is_not_echoed_or_parsed(monkeypatch):
    monkeypatch.setattr(runtime, "MAX_VISUAL_RESPONSE_BYTES", 8)
    assert runtime.structured_response_summary("responses", b"x" * 9) == {}


def test_truncated_response_stops_batch_without_adoption_and_keeps_safe_summary(
    desktop_paths, tmp_path
):
    provider = BudgetProvider(
        model_id="deepseek-v4-pro", base_url=OFFICIAL_DEEPSEEK, vision=False
    )
    transport = TruncatingTransport()
    facade = _make_facade(
        desktop_paths, tmp_path, provider, transport, question_count=2
    )
    choices = _choices(facade, 2)
    plan = facade.word_semantic_tag_preview(choices, "semantic", provider.revision)

    result = facade.word_semantic_tag_run(
        plan["plan_id"], plan["revision"], confirmed=True
    )

    assert len(transport.calls) == 1
    assert result["finished"] is True
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["status"] == "failed"
    assert item["changed"] is False
    assert "输出上限" in item["note"]
    assert "推理" in item["note"]
    assert item["response_summary"] == {
        "status": "incomplete",
        "incomplete_reason": "max_output_tokens",
        "usage": {
            "input_tokens": 101,
            "output_tokens": 32000,
            "total_tokens": 32101,
        },
        "reasoning_tokens": 31900,
    }
    assert facade._word_questions().attribute_store.get(choices[0]["key"]) is None
    assert facade._word_questions().attribute_store.get(choices[1]["key"]) is None
    assert "SECRET" not in json.dumps(result, ensure_ascii=False)
