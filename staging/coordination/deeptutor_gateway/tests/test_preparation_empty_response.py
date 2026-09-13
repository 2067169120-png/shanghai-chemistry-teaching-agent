"""Completed empty responses are not DNS failures or usable lesson content."""

import json

import pytest
from test_desktop_preparation_provider import _context

from integrations.deeptutor_shchem_v1.desktop_preparation_provider import (
    DesktopPreparationProviderError,
    StructuredPreparationProvider,
)
from integrations.deeptutor_shchem_v1.model_provider_probe import ProbeTransportResponse
from integrations.deeptutor_shchem_v1.visual_provider_runtime import (
    VisualProviderRuntimeError,
    parse_structured_visual_response,
)


def envelope(style, content):
    if style == "responses":
        return {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": content}],
                }
            ],
        }
    return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}


@pytest.mark.parametrize("style", ["responses", "chat_completions"])
@pytest.mark.parametrize("content", ["", " \n\t"])
def test_empty_completed_response_has_specific_message_and_one_call(style, content):
    class Transport:
        calls = 0

        def send(self, *args, **kwargs):
            self.calls += 1
            return ProbeTransportResponse(
                http_status=200,
                content_type="application/json",
                content_encoding=None,
                body=json.dumps(envelope(style, content)).encode(),
                latency_ms=1,
                model_invoked=True,
            )

    transport = Transport()
    provider = StructuredPreparationProvider(
        _context(api_style=style), transport=transport
    )
    with pytest.raises(DesktopPreparationProviderError) as caught:
        provider.generate({"topic": "电解质的电离"})
    assert caught.value.code == "provider_response_empty"
    assert "没有提供可读取的备课正文" in caught.value.message_zh
    assert "未生成文件" in caught.value.message_zh
    assert "费用" in caught.value.message_zh
    assert "DNS" not in caught.value.message_zh
    assert caught.value.retryable is True
    assert transport.calls == 1


@pytest.mark.parametrize(
    "style,payload,code",
    [
        ("responses", {"status": "completed", "output": []}, "provider_response_empty"),
        (
            "responses",
            {
                "status": "completed",
                "output": [
                    {
                        "type": "reasoning",
                        "summary": [
                            {
                                "type": "summary_text",
                                "text": '{"title":"not a final answer"}',
                            }
                        ],
                    }
                ],
            },
            "provider_response_empty",
        ),
        (
            "chat_completions",
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": None,
                            "reasoning_content": '{"title":"not a final answer"}',
                        },
                    }
                ]
            },
            "provider_response_empty",
        ),
        (
            "responses",
            {"status": "incomplete", "output": []},
            "provider_response_incomplete",
        ),
        (
            "responses",
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "refusal", "refusal": "refused"}],
                    }
                ],
            },
            "provider_response_refused",
        ),
        ("responses", {"status": "completed"}, "provider_response_invalid"),
        ("chat_completions", {"choices": []}, "provider_response_invalid"),
        ("responses", envelope("responses", 42), "provider_response_invalid"),
        (
            "chat_completions",
            envelope("chat_completions", 42),
            "provider_response_invalid",
        ),
        ("responses", envelope("responses", "not JSON"), "provider_output_invalid"),
    ],
)
def test_empty_is_distinct_from_malformed_refused_and_incomplete(style, payload, code):
    with pytest.raises(VisualProviderRuntimeError) as caught:
        parse_structured_visual_response(style, json.dumps(payload).encode())
    assert caught.value.code == code


@pytest.mark.parametrize("style", ["responses", "chat_completions"])
def test_valid_json_still_parses(style):
    assert parse_structured_visual_response(
        style, json.dumps(envelope(style, '{"title":"chapter"}')).encode()
    )[0] == {"title": "chapter"}
