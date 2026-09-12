from __future__ import annotations

import json
import time
from contextlib import contextmanager

import pytest
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
    ) -> None:
        super().__init__(vision=vision)
        self.model_id = model_id
        self.base_url = base_url
        self.api_style = api_style

    def list_metadata(self):
        rows = super().list_metadata()
        rows[0]["model_id"] = self.model_id
        rows[0]["api_style"] = self.api_style
        return rows

    @contextmanager
    def borrow_invocation_context(self, profile_id: str, *, expected_revision: str):
        assert profile_id == "semantic"
        assert expected_revision == self.revision
        self.borrow_calls += 1
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
        )

    def invocation_policy(self, profile_id: str, *, expected_revision: str):
        policy = super().invocation_policy(
            profile_id, expected_revision=expected_revision
        )
        policy.update({"base_url": self.base_url, "model_id": self.model_id})
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
        model_id="deepseek-v4-pro", base_url=OFFICIAL_DEEPSEEK, vision=True
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
