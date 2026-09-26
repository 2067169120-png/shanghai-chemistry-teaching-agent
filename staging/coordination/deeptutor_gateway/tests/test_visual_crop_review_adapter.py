"""Synthetic transport tests for the mandatory second image-review stage."""

import base64
import io
import json
from copy import deepcopy
from dataclasses import replace

import pytest
from PIL import Image

from integrations.deeptutor_shchem_v1 import desktop_visual_import_adapters as adapters
from integrations.deeptutor_shchem_v1 import intake_batches_v2 as core
from integrations.deeptutor_shchem_v1.desktop_visual_crop_review import prepare_reviews
from integrations.deeptutor_shchem_v1.model_provider_probe import ProbeTransportResponse
from staging.coordination.deeptutor_gateway.tests.test_desktop_visual_schema import (
    _context,
    _lean_shard,
    _role_fragment,
)


class ReviewTransport:
    def __init__(self, fragment, mutation=None):
        self.fragment = fragment
        self.mutation = mutation
        self.calls = []

    def send(self, request, *, cancel_event, deadline_monotonic):
        assert not cancel_event.is_set()
        assert deadline_monotonic > 0
        body = json.loads(request.body)
        if request.api_style == "responses":
            content = body["input"][0]["content"]
            name = body["text"]["format"]["name"]
        else:
            content = next(item["content"] for item in body["messages"] if item["role"] == "user")
            name = body["response_format"]["json_schema"]["name"]
        payload = json.loads(content[0]["text"].split("\n", 1)[1])
        self.calls.append((name, payload, content))
        reply = deepcopy(self.fragment)
        if name == "shchem_visual_crop_review_v1":
            reply = {"request_digest": payload["request_digest"], "checks": [
                {**{key: item[key] for key in ("evidence_id", "page_sha256", "crop_sha256")},
                 "status": "pass", "reason": "合成裁片检查", "issue_codes": []}
                for item in payload["checks"]
            ]}
            if self.mutation:
                changed = self.mutation(reply)
                if changed is not None:
                    reply = changed
        raw = json.dumps(reply, ensure_ascii=False)
        response = ({"status": "completed", "output_text": raw}
                    if request.api_style == "responses" else
                    {"choices": [{"message": {"content": raw}, "finish_reason": "stop"}]})
        return ProbeTransportResponse(
            http_status=200, content_type="application/json", content_encoding=None,
            body=json.dumps(response).encode(), latency_ms=1, model_invoked=True,
        )


@pytest.mark.parametrize("style", ["responses", "chat_completions"])
@pytest.mark.parametrize("role", ["question", "answer", "handout"])
def test_both_api_styles_review_actual_crop_without_changing_fragment(style, role):
    shard = _lean_shard(role)
    fragment = _role_fragment(shard)
    before = deepcopy(fragment)
    transport = ReviewTransport(fragment)
    adapter = adapters.StructuredVisualShardProviderAdapter(
        _context(base_url="https://example.com/v1", api_style=style), transport=transport,
    )
    assert adapter.analyze_shard(shard) == before
    assert fragment == before
    assert [name for name, _, _ in transport.calls] == [
        "shchem_visual_observation_fragment_v2", "shchem_visual_crop_review_v1",
    ]
    review = transport.calls[1]
    image_parts = review[2][1:]
    images = [base64.b64decode((part["image_url"] if style == "responses" else
                               part["image_url"]["url"]).split(",", 1)[1]) for part in image_parts]
    expected = prepare_reviews(shard, core._validate_fragment(fragment, request=shard))[0]
    assert images == [raw for _, raw in expected.pages]
    assert images[0] == shard.pages[0].pixels
    assert len(images) == 1 + len(fragment["evidence"])
    with Image.open(io.BytesIO(images[-1])) as actual, Image.open(io.BytesIO(images[0])) as source:
        bounds = review[1]["checks"][-1]["pixel_xyxy"]
        assert actual.tobytes() == source.crop(bounds).tobytes()


@pytest.mark.parametrize("status", ["reject", "uncertain"])
def test_semantic_failure_never_returns_a_fragment_or_retries(status):
    shard = _lean_shard("question")
    def fail(reply):
        reply["checks"][0].update(status=status, issue_codes=["incomplete_visual"])
    transport = ReviewTransport(_role_fragment(shard), fail)
    adapter = adapters.StructuredVisualShardProviderAdapter(_context(), transport=transport)
    with pytest.raises(adapters.DesktopVisualImportAdapterError) as caught:
        adapter.analyze_shard(shard)
    assert caught.value.code == "visual_crop_review_failed"
    assert len(transport.calls) == 2


@pytest.mark.parametrize("case", ["missing", "duplicate", "hash", "digest", "extra", "fake_pass"])
def test_incomplete_or_cross_bound_response_does_not_pass(case):
    shard = _lean_shard("question")
    def mutate(reply):
        if case == "missing":
            reply["checks"].clear()
        elif case == "duplicate":
            reply["checks"].append(deepcopy(reply["checks"][0]))
        elif case == "hash":
            reply["checks"][0]["crop_sha256"] = "0" * 64
        elif case == "digest":
            reply["request_digest"] = "0" * 64
        elif case == "extra":
            reply["teacher_confirmed"] = True
        else:
            reply["checks"][0]["issue_codes"] = ["missing_options"]
    transport = ReviewTransport(_role_fragment(shard), mutate)
    adapter = adapters.StructuredVisualShardProviderAdapter(_context(), transport=transport)
    with pytest.raises(adapters.DesktopVisualImportAdapterError) as caught:
        adapter.analyze_shard(shard)
    assert caught.value.code == "visual_crop_review_invalid"
    assert len(transport.calls) == 2


def test_nine_evidence_rows_use_two_bounded_reviews_not_nine_hidden_retries():
    shard = _lean_shard("question")
    fragment = _role_fragment(shard)
    evidence = fragment["evidence"][0]
    fragment["evidence"] = [{**deepcopy(evidence), "evidence_id": evidence["evidence_id"]
                             if index == 0 else f"EV-SYNTHETIC-{index}"} for index in range(9)]
    transport = ReviewTransport(fragment)
    adapter = adapters.StructuredVisualShardProviderAdapter(_context(), transport=transport)
    assert adapter.analyze_shard(shard) == fragment
    assert len(transport.calls) == 3
    assert [len(row[1]["checks"]) for row in transport.calls[1:]] == [8, 1]


def test_cancel_between_first_pass_and_review_does_not_send_another_request(monkeypatch):
    shard = _lean_shard("question")
    transport = ReviewTransport(_role_fragment(shard))
    state = {"cancelled": False}
    def prepare(*args):
        batches = prepare_reviews(*args)
        state["cancelled"] = True
        return batches
    monkeypatch.setattr(adapters, "prepare_reviews", prepare)
    adapter = adapters.StructuredVisualShardProviderAdapter(
        _context(), transport=transport, should_cancel=lambda: state["cancelled"],
    )
    with pytest.raises(adapters.DesktopVisualImportAdapterError) as caught:
        adapter.analyze_shard(shard)
    assert caught.value.code == "visual_crop_review_cancelled"
    assert len(transport.calls) == 1


def test_empty_observation_on_a_nonempty_page_cannot_skip_review():
    shard = _lean_shard("question")
    fragment = _role_fragment(shard)
    fragment.update(evidence=[], paper_identity=None, theme_fragments=[], answer_candidates=[])
    transport = ReviewTransport(fragment)
    adapter = adapters.StructuredVisualShardProviderAdapter(_context(), transport=transport)
    with pytest.raises(adapters.DesktopVisualImportAdapterError) as caught:
        adapter.analyze_shard(shard)
    assert caught.value.code == "visual_crop_review_invalid"
    assert len(transport.calls) == 1


def test_live_cancellation_during_review_connect_prevents_post():
    shard = _lean_shard("question")
    state = {"cancelled": False, "review_posts": 0}
    class CancellingTransport(ReviewTransport):
        def send(self, request, *, cancel_event, deadline_monotonic):
            if self.calls:
                state["cancelled"] = True  # Simulated cancel during connect, before POST.
                assert cancel_event.is_set()  # Must reflect the live UI worker state.
                if cancel_event.is_set():
                    raise RuntimeError("synthetic transport stopped before POST")
                state["review_posts"] += 1
            return super().send(request, cancel_event=cancel_event, deadline_monotonic=deadline_monotonic)
    transport = CancellingTransport(_role_fragment(shard))
    adapter = adapters.StructuredVisualShardProviderAdapter(
        _context(), transport=transport, should_cancel=lambda: state["cancelled"],
    )
    with pytest.raises(adapters.DesktopVisualImportAdapterError) as caught:
        adapter.analyze_shard(shard)
    assert caught.value.code == "visual_crop_review_cancelled"
    assert state["review_posts"] == 0
    assert len(transport.calls) == 1


def test_blank_crop_is_rejected_locally_before_paid_review():
    shard = _lean_shard("question")
    stream = io.BytesIO()
    Image.new("RGB", (24, 32), "white").save(stream, "PNG")
    raw = stream.getvalue()
    shard = replace(shard, pages=(replace(shard.pages[0], pixels=raw,
        width=24, height=32, page_sha256=core.sha256_bytes(raw)),))
    transport = ReviewTransport(_role_fragment(shard))
    adapter = adapters.StructuredVisualShardProviderAdapter(_context(), transport=transport)
    with pytest.raises(adapters.DesktopVisualImportAdapterError) as caught:
        adapter.analyze_shard(shard)
    assert caught.value.code == "visual_crop_review_blank_crop"
    assert len(transport.calls) == 1


def test_review_transport_error_is_sanitized_and_not_retried():
    shard = _lean_shard("question")
    def explode(_reply):
        raise RuntimeError("synthetic-secret-must-not-leak")
    transport = ReviewTransport(_role_fragment(shard), explode)
    adapter = adapters.StructuredVisualShardProviderAdapter(_context(), transport=transport)
    with pytest.raises(adapters.DesktopVisualImportAdapterError) as caught:
        adapter.analyze_shard(shard)
    assert caught.value.code == "visual_crop_review_failed"
    assert "synthetic-secret" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert len(transport.calls) == 2
