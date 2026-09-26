"""Structural availability never stands in for a native Word selection proof."""

import hashlib
from copy import deepcopy

import pytest

import test_native_word_mapping as fixture
from integrations.deeptutor_shchem_v1 import desktop_native_word_compatibility as compatibility
from integrations.deeptutor_shchem_v1 import desktop_native_word_mapping as mapping


def payload(data, body):
    locations = [{
        "location_id": f"synthetic-{i}", "xml_locator": fixture.locator(node, body),
        "kind": "image" if node.tag == fixture.A + "blip" else "omml", "source_states": [],
        "context_text": "Synthetic original context", "assets": [{"synthetic_only": True}],
    } for i, node in enumerate(fixture.hosts(body))]
    return {"source_sha256": hashlib.sha256(data).hexdigest(), "source_revision": "r" * 64,
            "blocks": [{"block_index": 1, "locations": locations}]}


def test_one_inspection_covers_all_occurrences_and_does_not_verify_word(monkeypatch):
    body = fixture.make_body()
    data = fixture.docx(body)
    source = payload(data, body)
    actual = mapping.inspect_source
    calls = []
    def inspect(raw):
        calls.append(raw)
        return actual(raw)
    monkeypatch.setattr(mapping, "inspect_source", inspect)
    result = compatibility.attach_native_selection_support(source, data)
    assert calls == [data]
    assert result["native_selection_summary"]["available_locations"] == 6
    assert result["native_selection_summary"]["native_range_verified"] is False
    assert result["native_selection_summary"]["word_opened"] is False
    for location in result["blocks"][0]["locations"]:
        assert compatibility.location_availability(result, location)[0]
        assert "实际选区" in compatibility.location_availability(result, location)[1]


def test_whole_document_failure_disables_every_target_but_preserves_context_and_assets():
    body = fixture.make_body()
    body.insert(0, fixture.paragraph(fixture.node(fixture.W + "fldSimple", **{fixture.W + "instr": "PAGE"})))
    data = fixture.docx(body)
    source = payload(data, body)
    before = deepcopy(source)
    result = compatibility.attach_native_selection_support(source, data)
    assert result["native_selection_summary"]["available_locations"] == 0
    assert result["native_selection_summary"]["first_source_issue"] == "unsupported_active_or_revision_content"
    for old, location in zip(before["blocks"][0]["locations"], result["blocks"][0]["locations"]):
        assert all(location[k] == v for k, v in old.items())
        available, message = compatibility.location_availability(result, location)
        assert available is False and "文件包含域" in message and "复制位置" in message


def test_table_math_cannot_inherit_a_supported_image_result():
    body = fixture.make_body()
    body.insert(0, fixture.table([[fixture.paragraph(fixture.equation())]]))
    data = fixture.docx(body)
    result = compatibility.attach_native_selection_support(payload(data, body), data)
    locations = result["blocks"][0]["locations"]
    assert locations[0]["native_selection"]["code"] == "table_math_has_no_unique_range_identity"
    assert compatibility.location_availability(result, locations[0])[0] is False
    assert compatibility.location_availability(result, locations[1])[0] is True


@pytest.mark.parametrize("case", ["source_hash", "source_revision", "unexpected_exception"])
def test_unknown_or_unbound_preflight_leaves_viewer_available(monkeypatch, case):
    body = fixture.make_body()
    data = fixture.docx(body)
    source = payload(data, body)
    if case == "source_hash":
        source["source_sha256"] = "0" * 64
    elif case == "source_revision":
        source["source_revision"] = ""
    else:
        def fail(_raw):
            raise RuntimeError("PRIVATE PATH AND SOURCE XML MUST NOT ESCAPE")
        monkeypatch.setattr(mapping, "inspect_source", fail)
    result = compatibility.attach_native_selection_support(source, data)
    assert result["native_selection_summary"]["available_locations"] == 0
    for location in result["blocks"][0]["locations"]:
        assert location["context_text"] == "Synthetic original context"
        assert not compatibility.location_availability(result, location)[0]
        assert "PRIVATE" not in location["native_selection"]["message_zh"]


def test_availability_cannot_be_reused_for_a_different_source_revision_or_occurrence():
    body = fixture.make_body()
    data = fixture.docx(body)
    source = compatibility.attach_native_selection_support(payload(data, body), data)
    location = source["blocks"][0]["locations"][0]
    for key in ("source_sha256", "source_revision"):
        changed = deepcopy(source)
        changed[key] = "new-version"
        assert not compatibility.location_availability(changed, location)[0]
    other = deepcopy(source["blocks"][0]["locations"][1])
    other["native_selection"] = location["native_selection"]
    assert not compatibility.location_availability(source, other)[0]


def test_no_objects_do_not_trigger_a_pointless_package_inspection(monkeypatch):
    def fail(_data):
        raise AssertionError("No objects to select")
    monkeypatch.setattr(mapping, "inspect_source", fail)
    data = b"synthetic-empty-response"
    source = {"source_sha256": hashlib.sha256(data).hexdigest(), "source_revision": "current", "blocks": []}
    result = compatibility.attach_native_selection_support(source, data)
    assert result["native_selection_summary"]["locations_checked"] == 0
    assert result["native_selection_summary"]["native_range_verified"] is False


@pytest.mark.parametrize("case", [
    "old_schema", "missing_kind", "unknown_kind", "missing_table_start",
    "invalid_table_start", "duplicate_locator", "missing_locator", "wrong_source", "not_an_object",
])
def test_malformed_internal_inspection_blocks_selection_without_losing_viewer(monkeypatch, case):
    body = fixture.make_body()
    data = fixture.docx(body)
    inspection = mapping.inspect_source(data)
    obj = inspection["objects"][0]
    if case == "old_schema":
        inspection["schema"] = "old-projection"
    elif case == "missing_kind":
        del obj["kind"]
    elif case == "unknown_kind":
        obj["kind"] = "unknown-kind"
    elif case == "missing_table_start":
        del obj["table_start"]
    elif case == "invalid_table_start":
        obj["table_start"] = True
    elif case == "duplicate_locator":
        inspection["objects"].append(deepcopy(obj))
    elif case == "missing_locator":
        del obj["xml_locator"]
    elif case == "wrong_source":
        inspection["source_sha256"] = "0" * 64
    else:
        inspection["objects"][0] = None
    monkeypatch.setattr(mapping, "inspect_source", lambda _data: inspection)
    source = payload(data, body)
    before = deepcopy(source)
    result = compatibility.attach_native_selection_support(source, data)
    assert result["native_selection_summary"]["first_source_issue"] == "preflight_unavailable"
    assert result["native_selection_summary"]["available_locations"] == 0
    for old, location in zip(before["blocks"][0]["locations"], result["blocks"][0]["locations"]):
        assert all(location[k] == v for k, v in old.items())
        assert not compatibility.location_availability(result, location)[0]


def test_valid_projection_cannot_enable_a_different_object_kind():
    body = fixture.make_body()
    data = fixture.docx(body)
    source = payload(data, body)
    location = source["blocks"][0]["locations"][0]
    location["kind"] = "omml" if location["kind"] == "image" else "image"
    result = compatibility.attach_native_selection_support(source, data)
    assert not compatibility.location_availability(result, location)[0]
    assert result["native_selection_summary"]["available_locations"] == 5
