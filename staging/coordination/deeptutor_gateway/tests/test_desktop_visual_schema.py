from __future__ import annotations

import base64
import io
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from PIL import Image

from integrations.deeptutor_shchem_v1 import desktop_visual_import_adapters as adapters
from integrations.deeptutor_shchem_v1 import desktop_visual_schema as wire
from integrations.deeptutor_shchem_v1 import intake_batches_v2 as core
from integrations.deeptutor_shchem_v1.model_provider_probe import ProbeTransportResponse
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    ModelProviderProbeContext,
)
from staging.coordination.deeptutor_gateway.tests.test_desktop_visual_import_v2 import (
    _lean_answer_fragment,
    _lean_question_fragment,
    _lean_shard,
)


def _context(**changes: Any) -> ModelProviderProbeContext:
    return replace(
        ModelProviderProbeContext(
            profile_id="synthetic-schema-test",
            provider_id="openai_compatible",
            model_id="deepseek-v4-flash-vision-exp",
            base_url_policy="openai_compatible_public_https_v1",
            base_url="https://api.deepseek.com",
            revision="synthetic-revision",
            api_key="synthetic-test-key",
            api_style="responses",
        ),
        **changes,
    )


def _walk(value: Any):
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def test_wire_schema_expands_nested_refs_and_keeps_every_closed_object() -> None:
    canonical = core.intake_batch_visual_fragment_v2_schema()
    before = deepcopy(canonical)
    adapted = wire.desktop_visual_wire_schema(_context(), canonical)
    Draft202012Validator.check_schema(adapted)
    assert canonical == before
    assert canonical is not adapted
    for node in _walk(adapted):
        assert not {"$schema", "$defs", "$ref"}.intersection(node)
        assert not isinstance(node.get("type"), list)
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
        if "enum" in node and all(isinstance(value, str) for value in node["enum"]):
            assert node["type"] == "string"
    props = adapted["properties"]
    for gate in ("fallback_used", "source_text_layer_used"):
        assert props[gate] == {"type": "boolean", "enum": [False]}
    theme = props["theme_fragments"]["items"]["properties"]
    printed = theme["printed_questions"]["items"]["properties"]
    atomic = printed["atomic_parts"]["items"]["properties"]
    assert atomic["part_label"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert atomic["options"]["items"]["properties"]["chemical_expressions"]["items"][
        "properties"
    ]["status"] == {"enum": ["observed", "uncertain"], "type": "string"}


@pytest.mark.parametrize(
    "changes",
    [
        {"base_url": "https://api.openai.com/v1"},
        {"base_url": "https://api.deepseek.com.example.org"},
        {"base_url": "https://example.org/api.deepseek.com"},
        {"base_url": "http://api.deepseek.com"},
        {"base_url": "https://api.deepseek.com:8443"},
        {"api_style": "chat_completions"},
        {"api_style": ""},
        {"model_id": "deepseek-v4-flash"},
        {"model_id": "deepseek-v4-pro"},
        {"model_id": "some-other-vision-model"},
    ],
)
def test_other_routes_preserve_exact_original_schema(changes: dict[str, Any]) -> None:
    schema = core.intake_batch_visual_fragment_v2_schema()
    original = json.dumps(schema, ensure_ascii=False)
    context = _context(**changes)
    assert wire.desktop_visual_wire_schema(context, schema) is schema
    assert json.dumps(schema, ensure_ascii=False) == original
    assert wire.visual_import_request_policy(
        context.base_url, context.model_id, context.api_style
    ) == {
        "max_output_tokens": 8000,
        "timeout_seconds": 90,
        "schema_dialect": "canonical-v2",
        "observation_prompt_version": "normalized-xywh-roles-pages-v4",
    }


@pytest.mark.parametrize(
    "base_url", ["https://api.deepseek.com/v1", "https://api.deepseek.com:443/"]
)
def test_official_endpoint_url_variants_are_supported(base_url: str) -> None:
    schema = core.intake_batch_visual_fragment_v2_schema()
    assert "$defs" not in wire.desktop_visual_wire_schema(
        _context(base_url=base_url), schema
    )
    assert wire.visual_import_request_policy(
        base_url, "deepseek-v4-flash-vision-exp", "responses"
    ) == {
        "max_output_tokens": 32000,
        "timeout_seconds": 300,
        "schema_dialect": "inline-v1",
        "observation_prompt_version": "normalized-xywh-roles-pages-v4",
    }


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://example.org/schema.json"},
        {"$ref": "#/properties/title", "properties": {"title": {"type": "string"}}},
        {"$ref": "#/$defs/missing"},
        {"$defs": {"loop": {"$ref": "#/$defs/loop"}}, "$ref": "#/$defs/loop"},
        {
            "$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}},
            "type": "object",
        },
        {"$defs": {"a": {"type": "string"}}, "$ref": "#/$defs/a", "enum": ["fixed"]},
        {"$defs": {"a/b": {"type": "string"}}, "$ref": "#/$defs/a/b"},
        {
            "$defs": {"unused": {"$ref": "https://example.org/schema.json"}},
            "type": "object",
        },
        {"type": "string", "pattern": "^must-keep-this-constraint$"},
        {"type": "object", "$dynamicRef": "#node"},
        {"type": ["string", "null"], "anyOf": [{"enum": ["fixed"]}]},
        {"type": ["integer", "null"]},
        {"$schema": "https://json-schema.org/draft-07/schema", "type": "object"},
    ],
)
def test_unsupported_schema_fails_without_dropping_constraints(
    schema: dict[str, Any],
) -> None:
    with pytest.raises(wire.DesktopVisualSchemaError):
        wire.desktop_visual_wire_schema(_context(), schema)


def test_schema_expansion_has_depth_and_output_growth_bounds() -> None:
    deep: dict[str, Any] = {"type": "string"}
    for _ in range(70):
        deep = {"type": "array", "items": deep}
    with pytest.raises(wire.DesktopVisualSchemaError, match="limit"):
        wire.desktop_visual_wire_schema(_context(), deep)
    definitions: dict[str, Any] = {"leaf": {"type": "string"}}
    previous = "leaf"
    for index in range(14):
        current = f"branch{index}"
        definitions[current] = {"anyOf": [{"$ref": f"#/$defs/{previous}"}] * 2}
        previous = current
    with pytest.raises(wire.DesktopVisualSchemaError, match="limit"):
        wire.desktop_visual_wire_schema(
            _context(), {"$defs": definitions, "$ref": f"#/$defs/{previous}"}
        )


@pytest.mark.parametrize("invalid", [True, False, -1, 0.0, 1.5, "0", None, []])
def test_wire_schema_rejects_invalid_max_items(invalid: Any) -> None:
    schema = {"type": "array", "items": {"type": "string"}, "maxItems": invalid}
    with pytest.raises(wire.DesktopVisualSchemaError, match="maxItems"):
        wire.desktop_visual_wire_schema(_context(), schema)


@pytest.mark.parametrize("maximum", [0, 1, 3])
def test_wire_schema_preserves_nonnegative_integer_max_items(maximum: int) -> None:
    schema = {"type": "array", "items": {"type": "string"}, "maxItems": maximum}
    adapted = wire.desktop_visual_wire_schema(_context(), schema)
    assert adapted == schema
    validator = Draft202012Validator(adapted)
    assert validator.is_valid(["synthetic"] * maximum)
    assert not validator.is_valid(["synthetic"] * (maximum + 1))


def _fragments() -> list[dict[str, Any]]:
    question = _lean_question_fragment(_lean_shard("question"))
    theme = question["theme_fragments"][0]
    printed = theme["printed_questions"][0]
    atomic = printed["atomic_parts"][0]
    printed["options"] = [
        {
            "label": "A",
            "content": "合成测试选项",
            "chemical_expressions": deepcopy(atomic["chemical_expressions"]),
            "visual_object_refs": [],
            "evidence_refs": atomic["evidence_refs"],
        }
    ]
    atomic["options"] = deepcopy(printed["options"])
    theme["dependency_edges"] = [
        {
            "dependency_edge_id": "EDGE-SYNTHETIC",
            "from_atomic_part_id": atomic["atomic_part_id"],
            "to_atomic_part_id": atomic["atomic_part_id"],
            "relation": "uses_prior_result",
            "evidence_refs": atomic["evidence_refs"],
        }
    ]
    # Use a contract enum, not an inferred chemistry dependency.
    theme["dependency_edges"][0]["relation"] = (
        core.intake_batch_visual_fragment_v2_schema()["$defs"]["dependency_edge"][
            "properties"
        ]["relation"]["enum"][0]
    )
    nullable = deepcopy(question)
    nullable["paper_identity"]["source_year"] = "unknown"
    nullable["theme_fragments"][0]["printed_questions"][0]["atomic_parts"][0][
        "part_label"
    ] = None
    return [question, nullable, _lean_answer_fragment(_lean_shard("answer"))]


def _object_paths(value: Any, path: tuple[Any, ...] = ()):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from _object_paths(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _object_paths(child, (*path, index))


def _at(value: Any, path: tuple[Any, ...]) -> Any:
    for key in path:
        value = value[key]
    return value


def test_canonical_and_wire_accept_the_same_valid_and_invalid_fragments() -> None:
    schema = core.intake_batch_visual_fragment_v2_schema()
    canonical = Draft202012Validator(schema)
    adapted = Draft202012Validator(wire.desktop_visual_wire_schema(_context(), schema))
    for fragment in _fragments():
        canonical.validate(fragment)
        adapted.validate(fragment)
        for path, obj in _object_paths(fragment):
            extra = deepcopy(fragment)
            _at(extra, path)["unexpected_property"] = "must be rejected"
            assert not canonical.is_valid(extra), path
            assert not adapted.is_valid(extra), path
            for key in obj:
                missing = deepcopy(fragment)
                del _at(missing, path)[key]
                assert not canonical.is_valid(missing), (*path, key)
                assert not adapted.is_valid(missing), (*path, key)
        for gate in ("source_text_layer_used", "fallback_used"):
            for invalid_value in (True, 0, "false", None):
                invalid = deepcopy(fragment)
                invalid[gate] = invalid_value
                assert not canonical.is_valid(invalid)
                assert not adapted.is_valid(invalid)
    for path, value in [
        (("source_role",), "not-a-role"),
        (("input_mode",), "ocr"),
        (("paper_identity", "source_year"), "2025"),
        (("theme_fragments", 0, "fragment_position"), "not-a-position"),
        (
            (
                "theme_fragments",
                0,
                "printed_questions",
                0,
                "atomic_parts",
                0,
                "part_label",
            ),
            1,
        ),
        (
            (
                "theme_fragments",
                0,
                "printed_questions",
                0,
                "chemical_expressions",
                0,
                "status",
            ),
            "verified",
        ),
    ]:
        invalid = deepcopy(_fragments()[0])
        _at(invalid, path[:-1])[path[-1]] = value
        assert not canonical.is_valid(invalid), path
        assert not adapted.is_valid(invalid), path


class _CaptureTransport:
    def __init__(self, fragment: Mapping[str, Any]) -> None:
        self.requests: list[Any] = []
        self.deadlines: list[float] = []
        self.fragment = fragment

    def send(self, request: Any, *, cancel_event: Any, deadline_monotonic: float):
        assert not cancel_event.is_set()
        assert deadline_monotonic > 0
        self.requests.append(request)
        self.deadlines.append(deadline_monotonic)
        body = {
            "status": "completed",
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(self.fragment),
                        }
                    ]
                }
            ],
        }
        return ProbeTransportResponse(
            http_status=200,
            content_type="application/json",
            content_encoding=None,
            body=json.dumps(body).encode(),
            latency_ms=1,
            model_invoked=True,
        )


def _role_fragment(request: core.VisualShardRequest) -> dict[str, Any]:
    if request.source_role == "answer":
        return _lean_answer_fragment(request)
    fragment = _lean_question_fragment(request)
    fragment["source_role"] = request.source_role
    return fragment


def _wrong_role_fragment(request: core.VisualShardRequest) -> dict[str, Any]:
    fragment = _role_fragment(request)
    refs = [fragment["evidence"][0]["evidence_id"]]
    if request.source_role == "answer":
        # A schema-valid answer-page heading still must not create a theme,
        # even if it contains no printed questions at all.
        fragment["theme_fragments"] = [
            {
                "theme_big_question_id": "SYNTHETIC-ANSWER-HEADING",
                "theme_number": "一",
                "title": "合成答案页标题",
                "context": "合成答案分组",
                "sequence_in_paper": 1,
                "fragment_position": "complete",
                "shared_materials": [],
                "visual_objects": [],
                "dependency_edges": [],
                "printed_questions": [],
                "evidence_refs": refs,
            }
        ]
    else:
        fragment["answer_candidates"] = [
            {
                "answer_candidate_id": "SYNTHETIC-WRONG-ROLE-ANSWER",
                "question_number": "1",
                "part_label": None,
                "atomic_part_id": None,
                "answer_body": "纯合成答案",
                "chemical_expressions": [],
                "evidence_refs": refs,
            }
        ]
    return fragment


@pytest.mark.parametrize("official", [True, False])
@pytest.mark.parametrize("role", ["question", "answer", "handout"])
def test_role_schema_adds_only_the_existing_empty_array_gate(
    official: bool,
    role: str,
) -> None:
    context = _context() if official else _context(base_url="https://api.openai.com/v1")
    canonical = core.intake_batch_visual_fragment_v2_schema()
    before = deepcopy(canonical)
    forbidden = "theme_fragments" if role == "answer" else "answer_candidates"
    role_schema = wire.desktop_visual_role_schema(canonical, role)
    expected_role_schema = deepcopy(canonical)
    expected_role_schema["properties"][forbidden]["maxItems"] = 0
    assert role_schema == expected_role_schema
    assert (
        role_schema["properties"][forbidden]["items"]
        == canonical["properties"][forbidden]["items"]
    )
    assert canonical == before
    adapted = wire.desktop_visual_wire_schema(context, role_schema)
    expected_wire = deepcopy(wire.desktop_visual_wire_schema(context, canonical))
    expected_wire["properties"][forbidden]["maxItems"] = 0
    assert adapted == expected_wire
    assert all(node.get("items") is not False for node in _walk(adapted))
    Draft202012Validator.check_schema(adapted)
    validator = Draft202012Validator(adapted)
    request = _lean_shard(role)
    valid = _role_fragment(request)
    validator.validate(valid)
    invalid = _wrong_role_fragment(request)
    # This used to pass the outbound schema and fail only after model return.
    Draft202012Validator(canonical).validate(invalid)
    assert not validator.is_valid(invalid)
    for nonempty in ([None], [{}], [False]):
        invalid = deepcopy(valid)
        invalid[forbidden] = nonempty
        assert not validator.is_valid(invalid)
    missing = deepcopy(valid)
    del missing[forbidden]
    assert not validator.is_valid(missing)


@pytest.mark.parametrize("role", ["question", "answer", "handout"])
def test_wrong_role_response_is_rejected_locally_without_discarding_records(
    role: str,
) -> None:
    request = _lean_shard(role)
    invalid = _wrong_role_fragment(request)
    before = deepcopy(invalid)
    adapter = adapters.StructuredVisualShardProviderAdapter(
        _context(), transport=_CaptureTransport(invalid)
    )
    decoded = adapter.analyze_shard(request)
    assert decoded == before
    with pytest.raises(core.IntakeBatchV2Error) as raised:
        core._validate_fragment(decoded, request=request)
    assert raised.value.code == "provider_output_role_invalid"
    assert decoded == before


def test_role_schema_rejects_unknown_role_or_missing_required_array() -> None:
    with pytest.raises(wire.DesktopVisualSchemaError, match="source role"):
        wire.desktop_visual_role_schema(
            core.intake_batch_visual_fragment_v2_schema(), "unknown"
        )
    with pytest.raises(wire.DesktopVisualSchemaError, match="array is missing"):
        wire.desktop_visual_role_schema({"type": "object", "properties": {}}, "answer")


def _two_page_request(layout: str) -> core.VisualShardRequest:
    request = _lean_shard("question")
    pages = []
    for index, color in enumerate(((210, 40, 40), (40, 40, 210)), 1):
        output = io.BytesIO()
        standalone = layout == "independent_jpeg"
        Image.new("RGB", (24, 32), color).save(
            output, format="JPEG" if standalone else "PNG"
        )
        raw = output.getvalue()
        pages.append(
            replace(
                request.pages[0],
                source_file_id=f"SYNTHETIC-JPG-{index}"
                if standalone
                else "SYNTHETIC-PDF",
                source_order=index if standalone else 1,
                page_number=1 if standalone else index,
                mime_type="image/jpeg" if standalone else "image/png",
                pixels=raw,
                page_sha256=core.sha256_bytes(raw),
            )
        )
    return replace(request, pages=tuple(pages))


def _two_page_fragment(request: core.VisualShardRequest) -> dict[str, Any]:
    fragment = _lean_question_fragment(request)
    second = deepcopy(fragment["evidence"][0])
    second["evidence_id"] = "EV-SYNTHETIC-SECOND"
    for field in ("source_file_id", "source_role", "page_number", "page_sha256"):
        second[field] = request.pages[1].public_manifest()[field]
    fragment["evidence"].append(second)
    return fragment


@pytest.mark.parametrize("official", [True, False])
@pytest.mark.parametrize("layout", ["independent_jpeg", "single_pdf"])
def test_page_schema_preserves_exact_manifest_tuples_and_other_constraints(
    official: bool,
    layout: str,
) -> None:
    request = _two_page_request(layout)
    manifests = tuple(page.public_manifest() for page in request.pages)
    if layout == "independent_jpeg":
        assert [page["page_number"] for page in manifests] == [1, 1]
    else:
        assert [page["page_number"] for page in manifests] == [1, 2]
        assert manifests[0]["source_file_id"] == manifests[1]["source_file_id"]
    canonical = core.intake_batch_visual_fragment_v2_schema()
    schema = wire.desktop_visual_role_schema(canonical, "question")
    before = deepcopy(schema)
    restricted = wire.desktop_visual_page_schema(schema, manifests)
    assert schema == before
    branches = restricted["properties"]["evidence"]["items"]["anyOf"]
    assert len(branches) == 2
    fields = ("source_file_id", "source_role", "page_number", "page_sha256")
    for branch, manifest in zip(branches, manifests, strict=True):
        expected = deepcopy(schema["$defs"]["evidence"])
        for field in fields:
            expected["properties"][field] = {
                **expected["properties"][field],
                "type": "integer" if field == "page_number" else "string",
                "enum": [manifest[field]],
            }
        assert branch == expected
        assert (
            branch["properties"]["evidence_id"]
            == schema["$defs"]["evidence"]["properties"]["evidence_id"]
        )
    original_items = before["properties"]["evidence"]["items"]
    restored = deepcopy(restricted)
    restored["properties"]["evidence"]["items"] = original_items
    assert restored == before
    context = _context() if official else _context(base_url="https://api.openai.com/v1")
    adapted = wire.desktop_visual_wire_schema(context, restricted)
    unbound_wire = wire.desktop_visual_wire_schema(context, schema)
    restored_wire = deepcopy(adapted)
    restored_wire["properties"]["evidence"]["items"] = unbound_wire["properties"][
        "evidence"
    ]["items"]
    assert restored_wire == unbound_wire
    Draft202012Validator.check_schema(adapted)
    validator = Draft202012Validator(adapted)
    valid = _two_page_fragment(request)
    validator.validate(valid)
    core._validate_fragment(valid, request=request)
    for field in fields:
        if manifests[0][field] == manifests[1][field]:
            continue
        mixed = deepcopy(valid)
        mixed["evidence"][0][field] = manifests[1][field]
        assert not validator.is_valid(mixed), field
    for field, value in (
        ("source_role", "answer"),
        ("source_file_id", "UNKNOWN"),
        ("page_sha256", "0" * 64),
    ):
        invalid = deepcopy(valid)
        invalid["evidence"][0][field] = value
        assert not validator.is_valid(invalid), field
    invalid = deepcopy(valid)
    invalid["evidence"][1]["page_number"] = 2 if layout == "independent_jpeg" else 3
    Draft202012Validator(canonical).validate(invalid)
    assert not validator.is_valid(invalid)
    for mutate in ("extra_property", "missing_bbox"):
        invalid = deepcopy(valid)
        if mutate == "extra_property":
            invalid["evidence"][0]["unexpected"] = True
        else:
            del invalid["evidence"][0]["bbox"]
        assert not validator.is_valid(invalid)
    assert core.intake_batch_visual_fragment_v2_schema() == canonical


@pytest.mark.parametrize("official", [True, False])
def test_adapter_binds_independent_jpeg_page_one_and_never_rewrites_returned_identity(
    official: bool,
) -> None:
    request = _two_page_request("independent_jpeg")
    fragment = _two_page_fragment(request)
    fragment["evidence"][1]["page_number"] = 2
    before = deepcopy(fragment)
    transport = _CaptureTransport(fragment)
    context = _context() if official else _context(base_url="https://api.openai.com/v1")
    adapter = adapters.StructuredVisualShardProviderAdapter(
        context, transport=transport
    )
    decoded = adapter.analyze_shard(request)
    assert decoded == before
    with pytest.raises(core.IntakeBatchV2Error, match="not bound to pixels"):
        core._validate_fragment(decoded, request=request)
    assert decoded == before
    body = json.loads(transport.requests[0].body)
    content = body["input"][0]["content"]
    assert [
        base64.b64decode(row["image_url"].split(",", 1)[1]) for row in content[1:]
    ] == [page.pixels for page in request.pages]
    payload = json.loads(content[0]["text"].split("\n", 1)[1])
    contract = payload["page_identity_contract"]
    expected = [
        {
            field: getattr(page, field)
            for field in ("source_file_id", "source_role", "page_number", "page_sha256")
        }
        for page in request.pages
    ]
    assert contract["allowed_combinations"] == expected
    assert "该来源文件内部的页号，不是卷面印刷页码" in contract["instructions"]
    assert "本地不会改写、猜测或替换页号与哈希" in contract["instructions"]


@pytest.mark.parametrize(
    "mutate", ["empty", "missing_id", "boolean_page", "bad_hash", "ref_sibling"]
)
def test_page_schema_rejects_invalid_manifest_or_unexpected_item_contract(
    mutate: str,
) -> None:
    schema = core.intake_batch_visual_fragment_v2_schema()
    pages = [_lean_shard("question").pages[0].public_manifest()]
    if mutate == "empty":
        pages = []
    elif mutate == "missing_id":
        del pages[0]["source_file_id"]
    elif mutate == "boolean_page":
        pages[0]["page_number"] = True
    elif mutate == "bad_hash":
        pages[0]["page_sha256"] = "not-a-digest"
    else:
        schema["properties"]["evidence"]["items"]["maxProperties"] = 6
    with pytest.raises(wire.DesktopVisualSchemaError):
        wire.desktop_visual_page_schema(schema, pages)


@pytest.mark.parametrize("official", [True, False])
@pytest.mark.parametrize("role", ["question", "answer", "handout"])
def test_adapter_sends_strict_schema_and_pixel_only_prompt(
    official: bool, role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapters.time, "monotonic", lambda: 1000.0)
    request = _lean_shard(role)
    fragment = _role_fragment(request)
    transport = _CaptureTransport(fragment)
    context = _context() if official else _context(base_url="https://api.openai.com/v1")
    adapter = adapters.StructuredVisualShardProviderAdapter(
        context, transport=transport
    )
    assert adapter.analyze_shard(request) == fragment
    assert len(transport.requests) == 1
    body = json.loads(transport.requests[0].body)
    assert body["max_output_tokens"] == (32000 if official else 8000)
    assert transport.deadlines == [1300.0 if official else 1090.0]
    output = body["text"]["format"]
    assert output["strict"] is True
    assert output["schema"] == wire.desktop_visual_wire_schema(
        context,
        wire.desktop_visual_page_schema(
            wire.desktop_visual_role_schema(
                core.intake_batch_visual_fragment_v2_schema(), request.source_role
            ),
            tuple(page.public_manifest() for page in request.pages),
        ),
    )
    prompt = body["input"][0]["content"][0]["text"]
    assert '"fallback_allowed":false' in prompt
    assert '"source_text_layer_supplied":false' in prompt
    prompt_payload = json.loads(prompt.split("\n", 1)[1])
    assert (
        prompt_payload["observation_prompt_version"]
        == wire.visual_import_request_policy(
            context.base_url, context.model_id, context.api_style
        )["observation_prompt_version"]
    )
    role_contract = prompt_payload["role_contract"]
    forbidden = "theme_fragments" if role == "answer" else "answer_candidates"
    assert role_contract["source_role"] == role
    assert role_contract["required_empty_arrays"] == {forbidden: []}
    assert f"{forbidden} 必须为 []" in role_contract["instructions"]
    if role == "answer":
        assert (
            "答案页标题、章节标题、分组名和题号不得创建主题大题"
            in role_contract["instructions"]
        )
        assert "空题目主题也不允许" in role_contract["instructions"]
    image = body["input"][0]["content"][1]
    assert image["type"] == "input_image"
    assert (
        base64.b64decode(image["image_url"].split(",", 1)[1]) == request.pages[0].pixels
    )
    validated = core._validate_fragment(fragment, request=request)
    if role == "answer":
        assert validated["theme_fragments"] == []
        assert validated["answer_candidates"][0]["independently_verified"] is False
    else:
        atomic = validated["theme_fragments"][0]["printed_questions"][0][
            "atomic_parts"
        ][0]
        assert atomic["cognitive_difficulty"]["human_verified"] is False
        assert atomic["classification"]["item_type"] == "unknown"


def test_prompt_defines_normalized_xywh_and_correct_synthetic_example() -> None:
    request = _lean_shard("question")
    canonical_before = core.intake_batch_visual_fragment_v2_schema()
    wire_before = wire.desktop_visual_wire_schema(_context(), canonical_before)
    pixels_before = request.pages[0].pixels
    prompt = adapters.StructuredVisualShardProviderAdapter._prompt(request)
    payload = json.loads(prompt.split("\n", 1)[1])
    contract = payload["bbox_contract"]
    assert payload["observation_prompt_version"] == "normalized-xywh-roles-pages-v4"
    assert "归一化左上角 xywh 字典" in prompt
    assert contract["object_with_exact_keys"] == ["x", "y", "width", "height"]
    assert contract["coordinate_space"] == "normalized_xywh"
    assert contract["origin"] == "top_left"
    assert contract["pixel_to_normalized"] == {
        "x": "left / page_width",
        "y": "top / page_height",
        "width": "(right - left) / page_width",
        "height": "(bottom - top) / page_height",
    }
    assert contract["bounds"] == [
        "0 <= x < 1",
        "0 <= y < 1",
        "0 < width <= 1",
        "0 < height <= 1",
        "x + width <= 1.000001",
        "y + height <= 1.000001",
    ]
    assert "逐个核对所有 evidence.bbox" in contract["self_check"]
    assert "本地不会自动裁边、猜测坐标单位或转换不合规结果" in contract["self_check"]
    example = contract["synthetic_example"]
    assert "纯合成" in example["purpose"] and "禁止照抄" in example["purpose"]
    page, box = example["page_pixels"], example["box_pixels"]
    assert example["bbox"] == {
        "x": box["left"] / page["width"],
        "y": box["top"] / page["height"],
        "width": (box["right"] - box["left"]) / page["width"],
        "height": (box["bottom"] - box["top"]) / page["height"],
    }
    fragment = _lean_question_fragment(request)
    fragment["evidence"][0]["bbox"] = example["bbox"]
    assert (
        core._validate_fragment(fragment, request=request)["evidence"][0]["bbox"]
        == example["bbox"]
    )
    assert payload["page_manifests"] == [request.pages[0].public_manifest()]
    assert request.pages[0].pixels == pixels_before
    assert core.intake_batch_visual_fragment_v2_schema() == canonical_before
    assert wire.desktop_visual_wire_schema(_context(), canonical_before) == wire_before


@pytest.mark.parametrize(
    "bbox",
    [
        {"x": 100, "y": 400, "width": 600, "height": 600},
        {"x": -0.1, "y": 0.2, "width": 0.2, "height": 0.3},
        {"x": 0.1, "y": -0.2, "width": 0.2, "height": 0.3},
        {"x": 1, "y": 0.2, "width": 0.2, "height": 0.3},
        {"x": 0.1, "y": 1, "width": 0.2, "height": 0.3},
        {"x": 0.1, "y": 0.2, "width": 0, "height": 0.3},
        {"x": 0.1, "y": 0.2, "width": 0.2, "height": 0},
        {"x": 0.1, "y": 0.2, "width": 1.1, "height": 0.3},
        {"x": 0.1, "y": 0.2, "width": 0.2, "height": 1.1},
        {"x": 0.8, "y": 0.2, "width": 0.3, "height": 0.3},
        {"x": 0.1, "y": 0.8, "width": 0.2, "height": 0.3},
    ],
)
def test_invalid_returned_bbox_is_rejected_without_clamping_or_unit_conversion(
    bbox: dict[str, float],
) -> None:
    request = _lean_shard("question")
    fragment = _lean_question_fragment(request)
    fragment["evidence"][0]["bbox"] = deepcopy(bbox)
    adapter = adapters.StructuredVisualShardProviderAdapter(
        _context(), transport=_CaptureTransport(fragment)
    )
    decoded = adapter.analyze_shard(request)
    assert decoded["evidence"][0]["bbox"] == bbox
    with pytest.raises(core.IntakeBatchV2Error, match="bbox is out of bounds"):
        core._validate_fragment(decoded, request=request)
    assert decoded["evidence"][0]["bbox"] == bbox


@pytest.mark.parametrize("official", [True, False])
def test_default_transport_uses_the_approved_policy_timeout(
    official: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    timeouts: list[int] = []
    transport = _CaptureTransport({})

    def create_transport(*, total_timeout_seconds: int):
        timeouts.append(total_timeout_seconds)
        return transport

    monkeypatch.setattr(adapters, "PinnedVisualTransport", create_transport)
    context = _context() if official else _context(base_url="https://api.openai.com/v1")
    adapter = adapters.StructuredVisualShardProviderAdapter(context)
    assert adapter._transport is transport
    assert timeouts == [300 if official else 90]
    injected = _CaptureTransport({})
    adapter = adapters.StructuredVisualShardProviderAdapter(context, transport=injected)
    assert adapter._transport is injected
    assert timeouts == [300 if official else 90]


def test_adapter_rejects_unsupported_schema_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _CaptureTransport({})
    monkeypatch.setattr(
        adapters,
        "intake_batch_visual_fragment_v2_schema",
        lambda: {"$ref": "https://example.org/schema"},
    )
    adapter = adapters.StructuredVisualShardProviderAdapter(
        _context(), transport=transport
    )
    with pytest.raises(adapters.DesktopVisualImportAdapterError) as raised:
        adapter.analyze_shard(_lean_shard("question"))
    assert raised.value.code == "visual_schema_unsupported"
    assert transport.requests == []
