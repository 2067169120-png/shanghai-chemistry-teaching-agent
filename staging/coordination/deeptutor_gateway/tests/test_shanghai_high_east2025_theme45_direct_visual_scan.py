from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops

from integrations.deeptutor_shchem_v1 import (
    shanghai_high_east2025_theme45_direct_visual_scan as module,
)
from integrations.deeptutor_shchem_v1.master_direct_visual_scan import (
    MasterDirectVisualScanError,
)
from integrations.deeptutor_shchem_v1.master_wave1_workbench import (
    MasterWave1WorkbenchError,
    MasterWave1WorkbenchReader,
)
from integrations.deeptutor_shchem_v1.pudong2026_first_mock_theme_direct_visual_scan import (
    _Snapshot,
)

DB = Path(__file__).resolve().parents[4] / "sh-chem-db"
PACKAGE = DB / module.PRODUCT_RELATIVE
MANIFEST = (module.PRODUCT_RELATIVE / "candidate_manifest.json").as_posix()
CANDIDATES = (module.PRODUCT_RELATIVE / "question_candidates.jsonl").as_posix()
EVIDENCE = (module.PRODUCT_RELATIVE / "evidence_map.json").as_posix()


@pytest.fixture(scope="module")
def master_evidence():
    return MasterWave1WorkbenchReader(DB)._snapshot()


@pytest.fixture
def reader(master_evidence):
    # Reuse a verified read-only Master snapshot, never real personal state.
    master = SimpleNamespace(_snapshot=lambda: master_evidence)
    return module.ShanghaiHighEast2025Theme45DirectVisualScanReader(DB, master)


def _rows():
    return [json.loads(line) for line in (DB / CANDIDATES).read_bytes().splitlines()]


def _patch_file(reader, monkeypatch, path, raw):
    original = reader._verified_file

    def changed(root, relative):
        resolved, data = original(root, relative)
        return resolved, raw if relative == path else data

    monkeypatch.setattr(reader, "_verified_file", changed)


def _rebind(reader, monkeypatch, path, value, *, jsonl=False):
    # Mutation exists only in memory. Rebind test pins to reach semantic checks;
    # the real source files and production pinned hashes remain unchanged.
    raw = (
        b"\n".join(json.dumps(row, ensure_ascii=False).encode() for row in value)
        if jsonl
        else json.dumps(value, ensure_ascii=False).encode()
    )
    digest = hashlib.sha256(raw).hexdigest()
    manifest = json.loads((DB / MANIFEST).read_bytes())
    key = "candidate_file_sha256" if path == CANDIDATES else "evidence_map_sha256"
    constant = (
        "EXPECTED_RECORDS_SHA256" if path == CANDIDATES else "EXPECTED_EVIDENCE_SHA256"
    )
    manifest[key] = digest
    manifest_raw = json.dumps(manifest, ensure_ascii=False).encode()
    _patch_file(reader, monkeypatch, path, raw)
    _patch_file(reader, monkeypatch, MANIFEST, manifest_raw)
    monkeypatch.setattr(module, constant, digest)
    monkeypatch.setattr(
        module,
        "EXPECTED_MANIFEST_FILE_SHA256",
        hashlib.sha256(manifest_raw).hexdigest(),
    )


def _tree_fingerprint():
    return {
        path: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in PACKAGE.rglob("*")
        if path.is_file()
    }


def test_actual_validator_and_live_reader_do_not_write_source(monkeypatch):
    before = _tree_fingerprint()
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location(
        "shanghai_high_east_existing_validator_readonly",
        PACKAGE / "validate_candidate.py",
    )
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    report = validator.validate_package(PACKAGE, DB, write_report=False)
    assert report["errors"] == []
    assert (
        report["counts"]["question_records"] == report["counts"]["minimum_parts"] == 11
    )
    assert report["counts"]["source_assets"] == 3
    assert report["counts"]["crops"] == 28
    assert (
        report["counts"]["D_labels_assigned"]
        == report["counts"]["human_reviewed_parts"]
        == 0
    )
    catalog = module.ShanghaiHighEast2025Theme45DirectVisualScanReader(DB).catalog()
    assert catalog["product_id"] == module.PRODUCT_ID
    assert catalog["paper_id"] == module.PAPER_ID
    assert catalog["count"] == 11
    assert catalog["master_node_ids"] == list(module.EXPECTED_ATOMIC_IDS)
    assert catalog["integrity"]["output_binding_count"] == 30
    assert catalog["integrity"]["source_binding_count"] == 6
    assert catalog["integrity"]["manifest_self_sha256"] is None
    assert catalog["integrity"]["new_visual_or_human_review_performed"] is False
    assert catalog["coverage"]["direct_exact_overlap"] == 0
    assert _tree_fingerprint() == before


def test_all_28_crops_have_exact_explicit_source_pixels_and_roles(reader):
    snapshot = reader._snapshot()
    assert isinstance(snapshot, _Snapshot)
    assert len(snapshot.records) == len(snapshot.by_master_id) == 11
    assert len(snapshot.crop_by_id) == 28
    assert Counter(crop["role"] for crop in snapshot.crop_by_id.values()) == {
        "question": 11,
        "shared_material": 2,
        "answer": 11,
        "unknown": 4,
    }
    assert len(snapshot.answer_crop_ids) == 11
    assert len(snapshot.forbidden_crop_ids) == 15
    for crop in snapshot.crop_by_id.values():
        data = snapshot.output_bytes[crop["output_path"]]
        assert hashlib.sha256(data).hexdigest() == crop["sha256"]
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            assert image.size == (crop["width"], crop["height"])
            with Image.open(DB / crop["source_asset"]) as source:
                x, y, w, h = crop["crop_box"]
                expected = source.crop((x, y, x + w, y + h)).convert("RGB")
                assert (
                    ImageChops.difference(expected, image.convert("RGB")).getbbox()
                    is None
                )


def test_all_11_details_retain_exact_text_parents_answer_and_unknowns(reader):
    for original in _rows():
        part = original["parts"][0]
        detail = reader.detail(part["part_id"])
        hierarchy = detail["scan_hierarchy"]
        assert hierarchy["paper_id"] == module.PAPER_ID
        assert hierarchy["theme_id"] == module.THEME_IDS[hierarchy["theme_sequence"]]
        assert hierarchy["printed_question_id"] == original["question_id"]
        assert hierarchy["atomic_part_id"] == part["part_id"]
        assert hierarchy["printed_sequence"] == int(part["printed_number"])
        assert hierarchy["atomic_sequence_in_printed"] == 1
        assert (
            hierarchy["theme_title"]
            == original["source_locator"]["printed_theme_title"]
        )
        assert detail["visible_summary_zh"] == part["prompt_raw"]
        assert detail["response_requirement_zh"] == part["prompt_normalized"]
        answer = detail["reference_answer"]
        assert answer["reference_answer_text"] == part["answer_evidence"]["answer_text"]
        assert answer["source_authority"] == "nonofficial_reference"
        assert answer["independently_verified"] is False
        assert answer["reference_points"] is None
        assert answer["rubric_status"] == "absent_no_stepwise_rubric"
        assert (
            detail["answer_source_evidence"][0]["sha256"]
            == part["answer_evidence"]["source_page_refs"][0]["crop_sha256"]
        )
        assert detail["answer_source_evidence"][0]["evidence_role"] == "answer"
        assert (
            detail["source_identity"]["source_url"]
            == original["provenance"]["source_url"]
        )
        labels = detail["scan_classification"]
        assert labels["label_status"] == "partial_source_candidate"
        assert labels["primary_K"] is None and labels["supporting_K"] == []
        assert labels["knowledge_candidates"] == part["classification"]["knowledge_K"]
        assert labels["knowledge_candidates_K"] == [
            k["id"] for k in part["classification"]["knowledge_K"]
        ]
        assert detail["cognitive_difficulty"] == part["difficulty"]
        assert detail["cognitive_difficulty"]["cognitive_prelabel"] is None
        assert detail["dependency"]["status"] == "unknown_prior_dependency_not_recorded"
        assert detail["dependency"]["prior_atomic_part_ids"] == []
        assert all(
            value is False
            for key, value in detail["authority"].items()
            if key not in {"candidate_only", "read_only"}
        )
        rendered = json.dumps(detail)
        assert "C:\\" not in rendered and "output_path" not in rendered


def test_only_explicit_stimulus_edge_is_shared_not_unrelated_context(reader):
    evidence = json.loads((DB / EVIDENCE).read_bytes())
    source_rows = _rows()
    for original, relation in zip(
        source_rows, evidence["question_evidence"], strict=True
    ):
        detail = reader.detail(original["parts"][0]["part_id"])
        shared = [
            item
            for item in detail["evidence_descriptors"]
            if item["evidence_role"] == "shared_material"
        ]
        assert len(shared) == 1
        assert shared[0]["crop_id"] == module._crop_id(relation["shared_visual_ref"])
        assert detail["dependency"]["shared_material_crop_ids"] == [
            shared[0]["crop_id"]
        ]
        assert detail["shared_stimulus_text_zh"] == [
            item["raw_text"] for item in original["stimulus_blocks"]
        ]
        assert [item["raw_text"] for item in detail["shared_materials"]] == detail[
            "shared_stimulus_text_zh"
        ]
        unrelated = [
            ref
            for ref in original["source_locator"]["page_refs"]
            if ref["role"] == "context"
            and ref["crop_asset"] != relation["shared_visual_ref"]
        ]
        assert len(unrelated) == 1
        assert unrelated[0]["crop_sha256"] not in {
            item["sha256"] for item in detail["evidence_descriptors"]
        }
    snapshot = reader._snapshot()
    assert Counter(record["hierarchy"]["theme_id"] for record in snapshot.records) == {
        module.THEME_IDS[4]: 6,
        module.THEME_IDS[5]: 5,
    }


def test_all_13_allowed_crop_routes_return_bound_pixels(reader, monkeypatch):
    snapshot = reader._snapshot()
    monkeypatch.setattr(reader, "_snapshot", lambda: snapshot)
    seen = set()
    for record in snapshot.records:
        for descriptor in record["viewed_evidence"]:
            payload = reader.question_crop(
                record["hierarchy"]["atomic_part_id"], descriptor["crop_id"]
            )
            assert (
                payload.sha256
                == hashlib.sha256(payload.data).hexdigest()
                == descriptor["sha256"]
            )
            assert len(payload.data) == descriptor["bytes"]
            seen.add(descriptor["crop_id"])
    assert len(seen) == 13


def test_answer_boundary_and_other_node_images_are_denied(reader, monkeypatch):
    snapshot = reader._snapshot()
    monkeypatch.setattr(reader, "_snapshot", lambda: snapshot)
    node = module.EXPECTED_ATOMIC_IDS[0]
    for crop_id in snapshot.forbidden_crop_ids:
        with pytest.raises(MasterDirectVisualScanError) as caught:
            reader.question_crop(node, crop_id)
        assert caught.value.status == 403
    for index, role in ((1, "question"), (6, "shared_material")):
        crop_id = next(
            item["crop_id"]
            for item in snapshot.records[index]["viewed_evidence"]
            if item["evidence_role"] == role
        )
        with pytest.raises(MasterDirectVisualScanError) as caught:
            reader.question_crop(node, crop_id)
        assert caught.value.status == 404


@pytest.mark.parametrize(
    "node,status",
    [("../outside", 400), ("", 400), ("a/b", 400), ("SHEAST2025-M05-B-T4-Q1-P9", 404)],
)
def test_invalid_or_absent_node_fails_closed(reader, node, status):
    with pytest.raises(MasterDirectVisualScanError) as caught:
        reader.detail(node)
    assert caught.value.status == status


@pytest.mark.parametrize(
    "kind",
    [
        "manifest",
        "candidates",
        "evidence",
        "schema",
        "taxonomy",
        "vocabulary",
        "source",
        "question",
        "shared_material",
        "answer",
        "unknown",
    ],
)
def test_every_frozen_binding_is_rechecked_after_prior_success(
    reader, monkeypatch, kind
):
    snapshot = reader._snapshot()
    manifest = json.loads((DB / MANIFEST).read_bytes())
    paths = {
        "manifest": MANIFEST,
        "candidates": CANDIDATES,
        "evidence": EVIDENCE,
        "schema": manifest["shared_schema"],
        "taxonomy": manifest["taxonomy_asset"],
        "vocabulary": manifest["controlled_vocabulary_asset"],
        "source": manifest["source_assets"][0]["asset"],
    }
    path = (
        paths[kind]
        if kind in paths
        else next(
            crop["asset"]
            for crop in snapshot.crop_by_id.values()
            if crop["role"] == kind
        )
    )
    _patch_file(reader, monkeypatch, path, (DB / path).read_bytes() + b"changed")
    with pytest.raises(MasterDirectVisualScanError) as caught:
        reader.catalog()
    assert caught.value.code == "master_direct_scan_binding_mismatch"


@pytest.mark.parametrize(
    "mutation",
    [
        "role",
        "source_sha",
        "crop_box",
        "answer_edge",
        "gate",
        "source_identity",
        "duplicate",
        "part_count",
        "difficulty",
        "answer_authority",
    ],
)
def test_rebound_records_still_require_semantic_closure(reader, monkeypatch, mutation):
    rows = _rows()
    row, part = rows[0], rows[0]["parts"][0]
    if mutation == "role":
        row["source_locator"]["page_refs"][0]["role"] = "context"
    elif mutation == "source_sha":
        row["source_locator"]["page_refs"][0]["source_sha256"] = "f" * 64
    elif mutation == "crop_box":
        row["source_locator"]["page_refs"][0]["crop_box"][0] += 1
    elif mutation == "answer_edge":
        part["answer_evidence"]["source_page_refs"] = deepcopy(
            rows[1]["parts"][0]["answer_evidence"]["source_page_refs"]
        )
    elif mutation == "gate":
        row["gates"]["generation_allowed"] = True
    elif mutation == "source_identity":
        row["source_id"] = "file-other"
    elif mutation == "duplicate":
        rows[1] = deepcopy(rows[0])
    elif mutation == "part_count":
        row["parts"].append(deepcopy(part))
    elif mutation == "difficulty":
        part["difficulty"]["cognitive_prelabel"] = "D1"
    else:
        part["answer_evidence"]["authority"] = "official"
    _rebind(reader, monkeypatch, CANDIDATES, rows, jsonl=True)
    with pytest.raises(MasterDirectVisualScanError):
        reader.catalog()


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_type",
        "duplicate_type",
        "cross_question",
        "answer_as_shared",
        "unrelated_as_shared",
    ],
)
def test_sidecar_cannot_replace_explicit_roles(reader, monkeypatch, mutation):
    value = json.loads((DB / EVIDENCE).read_bytes())
    if mutation == "missing_type":
        value["item_type_contract"]["entries"].pop()
    elif mutation == "duplicate_type":
        value["item_type_contract"]["entries"][1] = deepcopy(
            value["item_type_contract"]["entries"][0]
        )
    elif mutation == "cross_question":
        value["question_evidence"][0]["question_crop"] = value["question_evidence"][1][
            "question_crop"
        ]
    elif mutation == "answer_as_shared":
        value["question_evidence"][0]["shared_visual_ref"] = value["question_evidence"][
            0
        ]["answer_crop"]
    else:
        current = value["question_evidence"][0]["shared_visual_ref"]
        value["question_evidence"][0]["shared_visual_ref"] = next(
            ref["crop_asset"]
            for ref in _rows()[0]["source_locator"]["page_refs"]
            if ref["role"] == "context" and ref["crop_asset"] != current
        )
    _rebind(reader, monkeypatch, EVIDENCE, value)
    with pytest.raises(MasterDirectVisualScanError):
        reader.catalog()


@pytest.mark.parametrize(
    "mutation",
    [
        "parent",
        "source",
        "printed_order",
        "atomic_order",
        "theme_title",
        "exact_overlap",
        "missing_master",
    ],
)
def test_master_identity_order_and_exact_disjointness(
    reader, monkeypatch, master_evidence, mutation
):
    snapshot = deepcopy(master_evidence)
    node = module.EXPECTED_ATOMIC_IDS[0]
    atom = snapshot.master_nodes[("atomic_part", node)]
    if mutation == "parent":
        atom["parent_theme_big_question_id"] = "THEME-other"
    elif mutation == "source":
        atom["source_id"] = "file-other"
    elif mutation == "printed_order":
        snapshot.master_nodes[("printed_question", module.EXPECTED_PRINTED_IDS[0])][
            "printed_question_order"
        ]["value"] = 99
    elif mutation == "atomic_order":
        atom["atomic_part_order"]["value"] = 2
    elif mutation == "theme_title":
        snapshot.master_nodes[("theme_big_question", module.THEME_IDS[4])][
            "theme_title"
        ]["value"] = "Other"
    elif mutation == "missing_master":
        snapshot.master_layers["atomic_part"].pop()
    else:
        snapshot.relations_by_master[node] = [
            {
                "relation_type": "exact_1_to_1",
                "identity_mapping_allowed": True,
                "endpoint_cardinality": {
                    "master_anchor_endpoints_for_strong_key": 0,
                    "master_atomic_endpoints_for_strong_key": 1,
                    "wave_rows_for_strong_key": 1,
                },
            }
        ]
    monkeypatch.setattr(reader.master_workbench, "_snapshot", lambda: snapshot)
    with pytest.raises(MasterDirectVisualScanError):
        reader.catalog()


def test_master_unavailable_has_adapter_error(reader, monkeypatch):
    def unavailable():
        raise MasterWave1WorkbenchError("unavailable", "test")

    monkeypatch.setattr(reader.master_workbench, "_snapshot", unavailable)
    with pytest.raises(MasterDirectVisualScanError) as caught:
        reader.catalog()
    assert caught.value.code == "master_direct_scan_master_identity_unavailable"


@pytest.mark.parametrize("path", ["../outside", "C:/outside", "missing.png"])
def test_unsafe_or_missing_bound_file_rejected(reader, path):
    with pytest.raises(MasterDirectVisualScanError):
        reader._bound(path, "0" * 64)


def test_last_moment_crop_corruption_is_denied(reader, monkeypatch):
    snapshot = reader._snapshot()
    node = module.EXPECTED_ATOMIC_IDS[0]
    descriptor = snapshot.by_master_id[node]["viewed_evidence"][0]
    crop = snapshot.crop_by_id[descriptor["crop_id"]]
    snapshot.output_bytes[crop["output_path"]] += b"changed"
    monkeypatch.setattr(reader, "_snapshot", lambda: snapshot)
    with pytest.raises(MasterDirectVisualScanError) as caught:
        reader.question_crop(node, descriptor["crop_id"])
    assert caught.value.code == "master_direct_scan_binding_mismatch"


def test_public_copies_cannot_mutate_later_reads(reader):
    node = module.EXPECTED_ATOMIC_IDS[0]
    detail = reader.detail(node)
    detail["authority"]["generation_allowed"] = True
    detail["scan_classification"]["knowledge_candidates_K"].append("K99")
    detail["shared_materials"][0]["crop_ids"].clear()
    again = reader.detail(node)
    assert again["authority"]["generation_allowed"] is False
    assert "K99" not in again["scan_classification"]["knowledge_candidates_K"]
    assert len(again["shared_materials"][0]["crop_ids"]) == 1
